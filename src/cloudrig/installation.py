"""Install the helper shipped with Cloudrig, outside the root process."""

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import distribution
from pathlib import Path

from cloudrig import __version__, state, updates
from cloudrig.broker import HELPER, check_service, trusted_root_path
from cloudrig.models import CrigError
from cloudrig.security import child_environment
from cloudrig.terminal import console, prompt_confirm

BUNDLE = Path(distribution("cloudrig").locate_file("cloudrig/_native"))


def bundled_files() -> Path:
    """Check the exact payload before asking for administrator authentication."""
    try:
        manifest = json.loads((BUNDLE / "manifest.json").read_text())
        system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
        if (manifest["version"], manifest["system"], manifest["arch"]) != (__version__, system, arch):
            raise ValueError("Bundle does not match this installation")
        for name in ("cloudrig-admin", "install-admin.sh", "uninstall-admin.sh"):
            expected = manifest["sha256"][name]
            if hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Bundle checksum mismatch")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CrigError(
            "Cloudrig's installation files are missing, damaged, or for another platform. "
            "Reinstall the matching release wheel. Source contributors can use crig setup --source PATH."
        ) from error
    return BUNDLE


def ensure_service(provider: str, source: Path | None = None) -> None:
    if source is None:
        folder = bundled_files()
        binary = folder / "cloudrig-admin"
        try:
            trusted_root_path(HELPER)
            check_service(provider)
            if hashlib.sha256(HELPER.read_bytes()).digest() == hashlib.sha256(binary.read_bytes()).digest():
                return
        except (OSError, CrigError):
            pass
    else:
        folder = source.expanduser().resolve()
        if not (folder / "scripts/install-admin.sh").is_file() or not (folder / "admin/go.mod").is_file():
            raise CrigError("Choose the Cloudrig source folder containing admin/ and scripts/.")
        if not shutil.which("go"):
            raise CrigError("Building from source requires Go 1.26+. Release wheels include the helper.")
        binary = folder / "dist/cloudrig-admin"

    if not sys.stdin.isatty():
        raise CrigError("Run crig in your own terminal to install or update its protected service.")
    updating = HELPER.exists()
    console.print(
        "\nUpdate API key protection" if updating else "\nProtect your API keys from AI agents",
        style="bold",
    )
    console.print()
    console.print(
        "Your saved keys and GPU records will stay."
        if updating
        else "Cloudrig stores your keys in a separate background service."
    )
    console.print(
        "Your Mac will ask for your login password."
        if sys.platform == "darwin"
        else "Your computer will ask for your sudo password."
    )
    console.print()
    prompt_confirm("Update now?" if updating else "Install now?")
    if source is not None:
        result = subprocess.run(
            ["/bin/sh", str(folder / "scripts/build-admin.sh")],
            cwd=folder,
            env=child_environment(),
            check=False,
        )
        if result.returncode:
            raise CrigError("Helper build failed. Fix the reported error and repeat crig setup.")
        folder = folder / "scripts"
    # Recheck packaged files after confirmation. No compiler or Python runs as root.
    if source is None:
        bundled_files()
    result = subprocess.run(
        ["/usr/bin/sudo", "-k", "--", "/bin/sh", str(folder / "install-admin.sh"), str(binary)],
        env=child_environment(),
        check=False,
    )
    if result.returncode:
        raise CrigError("Service installation failed. Fix the reported error and run crig again.")
    check_service(provider)


def update(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="crig update", description="Update Cloudrig and its protected service."
    )
    parser.add_argument("--wheel", type=Path, help="Install a downloaded release wheel")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        raise CrigError("Run crig update in your own terminal.")
    uv = shutil.which("uv")
    if not uv:
        raise CrigError("Install uv before updating Cloudrig.")
    release = None if args.wheel else updates.latest_release()
    if not args.wheel:
        if release is None:
            console.print("No published Cloudrig release is available yet.")
            return
        if updates.version_number(release.version) <= updates.version_number(__version__):
            ensure_service(state.load().get("provider") or "vastai")
            console.print(f"Cloudrig {__version__} is up to date.")
            return
    gh = shutil.which("gh")
    if not gh:
        raise CrigError("Install GitHub CLI to verify signed updates, then run crig update again.")
    with tempfile.TemporaryDirectory(prefix="cloudrig-update-") as temporary:
        if args.wheel:
            wheel = args.wheel.expanduser().resolve(strict=True)
            match = re.fullmatch(r"cloudrig-(\d+\.\d+\.\d+)-py3-none-[A-Za-z0-9_]+\.whl", wheel.name)
            if not match:
                raise CrigError("Choose a Cloudrig release wheel for your platform.")
            version = match[1]
        else:
            version = release.version
            manifest = json.loads((bundled_files() / "manifest.json").read_text())
            targets = {
                ("darwin", "arm64"): "macosx_13_0_arm64",
                ("darwin", "amd64"): "macosx_13_0_x86_64",
                ("linux", "arm64"): "manylinux_2_17_aarch64",
                ("linux", "amd64"): "manylinux_2_17_x86_64",
            }
            name = f"cloudrig-{version}-py3-none-{targets[manifest['system'], manifest['arch']]}.whl"
            if name not in release.assets:
                raise CrigError(
                    "The latest release has no wheel for this platform. Your installation is unchanged."
                )
            wheel = Path(temporary) / name
            result = subprocess.run(
                [
                    gh,
                    "release",
                    "download",
                    f"v{version}",
                    "--repo",
                    updates.REPOSITORY,
                    "--pattern",
                    name,
                    "--dir",
                    temporary,
                ],
                env=child_environment(),
                check=False,
            )
            if result.returncode:
                raise CrigError("Download failed. Your installation is unchanged. Try crig update later.")
        console.print(f"Verifying Cloudrig {version} from {updates.REPOSITORY}.")
        result = subprocess.run(
            [
                gh,
                "attestation",
                "verify",
                str(wheel),
                "--repo",
                updates.REPOSITORY,
                "--signer-workflow",
                f"{updates.REPOSITORY}/.github/workflows/release.yml",
                "--source-ref",
                f"refs/tags/v{version}",
                "--deny-self-hosted-runners",
            ],
            env=child_environment(),
            check=False,
        )
        if result.returncode:
            raise CrigError("Release signature verification failed. Your installation is unchanged.")
        result = subprocess.run(
            [uv, "tool", "install", "--force", str(wheel)], env=child_environment(), check=False
        )
        if result.returncode:
            raise CrigError("Package installation failed. Try crig update again.")
    executable = shutil.which("crig")
    if not executable:
        raise CrigError("Open a new terminal and run crig setup --service-only to finish the update.")
    result = subprocess.run([executable, "setup", "--service-only"], env=child_environment(), check=False)
    if result.returncode:
        raise CrigError("Cloudrig was updated. Run crig setup --service-only to finish service installation.")
    console.print("Cloudrig and its protected service are up to date. No API key was replaced.")


def uninstall(argv: list[str]) -> None:
    import argparse

    argparse.ArgumentParser(prog="crig uninstall", description="Remove Cloudrig and its service.").parse_args(
        argv
    )
    if not sys.stdin.isatty():
        raise CrigError("Run crig uninstall in your own terminal.")
    uv = shutil.which("uv")
    if not uv:
        raise CrigError("Install uv before removing the global Cloudrig installation.")
    folder = bundled_files()
    console.print("This removes Cloudrig and its service. It does not stop or delete cloud instances.")
    console.print("Credentials and recovery records remain in /var/db/cloudrig.")
    prompt_confirm("Remove Cloudrig? Check the provider console for resources that still cost money.")
    bundled_files()
    result = subprocess.run(
        ["/usr/bin/sudo", "-k", "--", "/bin/sh", str(folder / "uninstall-admin.sh")],
        env=child_environment(),
        check=False,
    )
    if result.returncode:
        raise CrigError("Service removal failed. Cloudrig remains installed; inspect the reported error.")
    result = subprocess.run([uv, "tool", "uninstall", "cloudrig"], env=child_environment(), check=False)
    if result.returncode:
        raise CrigError("Service removed. Run uv tool uninstall cloudrig to remove the command.")
