"""Include a native helper and its installer in each platform wheel."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        system = os.environ.get("GOOS", {"Darwin": "darwin", "Linux": "linux"}.get(platform.system()))
        arch = os.environ.get(
            "GOARCH", {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
        )
        tags = {
            ("darwin", "arm64"): "macosx_13_0_arm64",
            ("darwin", "amd64"): "macosx_13_0_x86_64",
            ("linux", "arm64"): "manylinux_2_17_aarch64",
            ("linux", "amd64"): "manylinux_2_17_x86_64",
        }
        if (system, arch) not in tags:
            raise RuntimeError("Cloudrig supports macOS and Linux on ARM64 or x86-64.")
        if not shutil.which("go"):
            raise RuntimeError(
                "Building from source requires Go. Install a release wheel to skip compilation."
            )
        self.bundle = tempfile.TemporaryDirectory(prefix="cloudrig-wheel-")
        folder = Path(self.bundle.name)
        root = Path(self.root)
        binary = folder / "cloudrig-admin"
        subprocess.run(
            ["go", "build", "-trimpath", "-ldflags=-s -w -buildid=", "-o", str(binary), "."],
            cwd=root / "admin",
            env={**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0"},
            check=True,
        )
        for name in ("install-admin.sh", "uninstall-admin.sh"):
            shutil.copyfile(root / "scripts" / name, folder / name)
        files = ("cloudrig-admin", "install-admin.sh", "uninstall-admin.sh")
        manifest = {
            "version": self.metadata.version,
            "system": system,
            "arch": arch,
            "sha256": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files},
        }
        (folder / "manifest.json").write_text(json.dumps(manifest) + "\n")
        for name in (*files, "manifest.json"):
            build_data["force_include"][str(folder / name)] = f"cloudrig/_native/{name}"
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{tags[system, arch]}"

    def finalize(self, version, build_data, artifact_path):
        if self.target_name == "wheel":
            self.bundle.cleanup()
