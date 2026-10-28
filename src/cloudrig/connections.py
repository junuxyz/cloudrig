import ipaddress
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from filelock import FileLock

from cloudrig import broker
from cloudrig.models import CrigError, Instance, RateLimited, safe_text
from cloudrig.security import check_transfer_path, child_environment, private_directory, regular_file
from cloudrig.state import atomic_write


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise CrigError(f"{binary} is not installed or on PATH. Run crig doctor for setup instructions.")
    return path


def run(argv: list[str]) -> None:
    command = [require(argv[0]), *argv[1:]]
    result = subprocess.run(command, check=False, env=child_environment())
    if result.returncode:
        raise CrigError(f"{argv[0]} exited with status {result.returncode}.")


def alias(instance: Instance) -> str:
    return f"crig-{instance.provider}-{instance.id}"


def validate_endpoint(instance: Instance) -> None:
    host, user, port = instance.host or "", instance.user, instance.port
    safe_text(host)
    safe_text(user)
    if "%" in host:
        raise CrigError("Scoped IPv6 addresses are not supported for cloud SSH endpoints.")
    try:
        ipaddress.ip_address(host)
        valid_host = True
    except ValueError:
        valid_host = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host))
    if not valid_host or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
        raise CrigError("Provider returned an invalid SSH hostname or user.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise CrigError("SSH port is not ready. Wait for the instance to start, then retry.")


def ssh_string(value: str) -> str:
    if any(char in value for char in "\n\r\x00"):
        raise CrigError("SSH paths cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def verify_grant(instance: Instance) -> tuple[Path, Path, Path]:
    if not instance.approved:
        raise CrigError(
            f"Authorize this GPU first: crig {instance.id} approve. This needs fresh sudo authentication."
        )
    if not all((instance.public_key, instance.agent_socket, instance.known_hosts)):
        raise CrigError(f"SSH setup is still in progress. Reconnect with crig {instance.id} ssh.")
    paths = [Path(value) for value in (instance.public_key, instance.agent_socket, instance.known_hosts)]
    root = broker.PUBLIC_ROOT.resolve()
    for path in paths:
        if not path.is_absolute() or path.is_symlink() or root not in path.resolve().parents:
            raise CrigError("Invalid public connection grant.")
        broker.trusted_root_path(path.parent)
    public, agent, known = paths
    if (
        public.suffix != ".pub"
        or public.stat().st_uid != broker.ROOT_UID
        or known.stat().st_uid != broker.ROOT_UID
    ):
        raise CrigError("Connection grants must be published by the root-owned helper.")
    if not agent.is_socket():
        raise CrigError("The approved SSH signer is not ready. Retry shortly.")
    return public, agent, known


def install_alias(instance: Instance) -> str:
    validate_endpoint(instance)
    public, agent, known = verify_grant(instance)
    ssh_dir = Path.home() / ".ssh"
    private_directory(ssh_dir)
    managed = ssh_dir / "cloudrig"
    private_directory(managed)
    regular_file(managed / ".lock")
    name = alias(instance)
    block = (
        f"Host {name}\n"
        f"  HostName {instance.host}\n"
        f"  User {instance.user}\n"
        f"  Port {instance.port}\n"
        f"  IdentityFile {ssh_string(str(public))}\n"
        "  IdentitiesOnly yes\n"
        f"  IdentityAgent {ssh_string(str(agent))}\n"
        "  ForwardAgent no\n"
        "  ForwardX11 no\n"
        "  ForwardX11Trusted no\n"
        "  PermitLocalCommand no\n"
        "  ProxyCommand none\n"
        "  ProxyJump none\n"
        "  AddKeysToAgent no\n"
        "  ControlMaster no\n"
        "  ControlPath none\n"
        "  ControlPersist no\n"
        "  KnownHostsCommand none\n"
        "  GlobalKnownHostsFile /dev/null\n"
        "  HostbasedAuthentication no\n"
        "  GSSAPIAuthentication no\n"
        "  PasswordAuthentication no\n"
        "  KbdInteractiveAuthentication no\n"
        "  UpdateHostKeys no\n"
        "  ConnectTimeout 10\n"
        "  ConnectionAttempts 1\n"
        "  ServerAliveInterval 30\n"
        "  ServerAliveCountMax 3\n"
        "  StrictHostKeyChecking yes\n"
        f"  HostKeyAlias {instance.provider}-{instance.id}\n"
        f"  UserKnownHostsFile {ssh_string(str(known))}\n"
    )
    with FileLock(managed / ".lock", timeout=10, mode=0o600):
        config = managed / "config"
        regular_file(config, private=True)
        contents = config.read_text() if config.exists() else ""
        blocks = re.split(r"(?=^Host )", contents, flags=re.MULTILINE)
        contents = "".join(part for part in blocks if part.splitlines()[:1] != [f"Host {name}"])
        candidate = managed / ".candidate"
        atomic_write(candidate, contents.rstrip() + "\n\n" + block if contents.strip() else block)
        check = subprocess.run(
            [require("ssh"), "-G", "-F", str(candidate), name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=child_environment(),
        )
        if check.returncode:
            candidate.unlink(missing_ok=True)
            raise CrigError("Generated SSH config did not pass OpenSSH validation.")
        candidate.replace(config)
        main = ssh_dir / "config"
        regular_file(main)
        original = main.read_text() if main.exists() else ""
        include = f"Include {ssh_string(str(config))}"
        if include not in original.splitlines():
            if original and not (managed / "config.before-cloudrig").exists():
                atomic_write(managed / "config.before-cloudrig", original)
            atomic_write(main, include + "\n\n" + original)
    return name


def ssh_command(name: str, *arguments: str, tty: bool = False) -> list[str]:
    config = Path.home() / ".ssh" / "cloudrig" / "config"
    argv = [require("ssh"), "-F", str(config)]
    if tty:
        argv.append("-t")
    return [*argv, name, *arguments]


def wait_ready(
    provider,
    identifier: str,
    timeout: float = 300,
    *,
    startup_script: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> Instance:
    deadline = time.monotonic() + timeout
    if progress:
        progress("Waiting for the GPU")
    while True:
        if time.monotonic() >= deadline:
            raise CrigError(
                f"Instance {identifier} remains rented; connection setup timed out. "
                f"Retry with crig {identifier} code."
            )
        try:
            item = provider.instance(identifier)
        except RateLimited as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CrigError(
                    f"Provider is still limiting requests. Instance {identifier} remains rented. "
                    f"Retry its connection with crig {identifier} code."
                ) from None
            if progress:
                progress(str(error))
            time.sleep(min(error.seconds, remaining))
            if progress:
                progress("Waiting for the GPU")
            continue
        if item.status in {"error", "failed", "terminated", "exited", "stopped"}:
            raise CrigError(f"Instance {identifier} is {item.status}. Inspect it with crig --rented.")
        if (
            item.host
            and item.port
            and item.direct
            and item.approved
            and item.agent_socket
            and Path(item.agent_socket).is_socket()
            and item.public_key
            and Path(item.public_key).is_file()
        ):
            name = install_alias(item)
            if progress:
                progress("Connecting over SSH")
            result = subprocess.run(
                [
                    require("ssh"),
                    "-F",
                    str(Path.home() / ".ssh" / "cloudrig" / "config"),
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    name,
                    "true",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                env=child_environment(),
            )
            if result.returncode == 0:
                if progress:
                    progress("Running startup script")
                run_startup(name, timeout=max(1, deadline - time.monotonic()), script=startup_script)
                if progress:
                    progress("SSH and startup ready")
                return item
        if time.monotonic() >= deadline:
            raise CrigError(
                f"Instance {identifier} is still rented; SSH is not ready. "
                f"Reconnect with crig {identifier} ssh. No second instance was created."
            )
        time.sleep(10)


def full_ssh(instance: Instance) -> None:
    if not instance.direct:
        raise CrigError("This pod exposes proxy SSH only. Expose 22/tcp for VS Code, commands, or rsync.")


def open_code(instance: Instance, remote_path: str) -> None:
    full_ssh(instance)
    name = install_alias(instance)
    if not remote_path.startswith("/") or any(ord(c) < 32 for c in remote_path):
        raise CrigError("Use an absolute remote workspace path, for example /workspace/project.")
    run(["code", "--remote", f"ssh-remote+{name}", remote_path])


# Current upstream security baseline, checked against rsync.samba.org/security.html.
MIN_RSYNC = (3, 5, 0)
SECRET_EXCLUDES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    "node_modules/",
    "dist/",
    ".env",
    ".env.*",
    ".ssh/",
    ".aws/",
    ".azure/",
    ".kube/",
    ".gnupg/",
    ".config/",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa*",
    "id_ed25519*",
    "cloudrig_ed25519*",
    "*.pem",
    "*.key",
    "credentials.json",
    "service-account*.json",
    "secrets.*",
    "*.secrets",
)


def upload_flags(local: Path) -> list[str]:
    patterns = list(SECRET_EXCLUDES)
    root = local if local.is_dir() else local.parent
    ignore = root / ".crigignore"
    if ignore.exists() or ignore.is_symlink():
        if ignore.is_symlink() or not ignore.is_file():
            raise CrigError(".crigignore must be a regular file inside the project.")
        for pattern in ignore.read_text().splitlines():
            pattern = pattern.strip()
            if pattern and not pattern.startswith("#"):
                if pattern.startswith("!"):
                    raise CrigError(".crigignore supports exclusion patterns only, without ! reinclusions.")
                patterns.append(pattern)
    git = shutil.which("git")
    in_repository = any((parent / ".git").exists() for parent in (root, *root.parents))
    if git:
        try:
            ignored = subprocess.run(
                [
                    git,
                    "-c",
                    "core.fsmonitor=false",
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                    "--directory",
                    "-z",
                    "--",
                    ".",
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=20,
                env=child_environment(),
            )
        except subprocess.TimeoutExpired as error:
            raise CrigError(
                "Git upload exclusions timed out. Check the repository before uploading."
            ) from error
        if ignored.returncode == 0:
            for name in os.fsdecode(ignored.stdout).split("\0"):
                if name:
                    escaped = re.sub(r"([\\*?\[])", r"\\\1", name)
                    patterns.append("/" + escaped)
        elif in_repository:
            raise CrigError("Cannot read Git upload exclusions. Repair Git before uploading.")
    elif in_repository:
        raise CrigError("Install git to apply this project's upload exclusions.")
    return [f"--filter=- {pattern}" for pattern in patterns]


def preview_upload(root: Path) -> None:
    from cloudrig.terminal import console

    check_rsync()
    flags = upload_flags(root)
    with tempfile.TemporaryDirectory(prefix="cloudrig-upload-") as target:
        result = subprocess.run(
            [
                require("rsync"),
                "-rln",
                "--safe-links",
                "--out-format=%n",
                *flags,
                "--",
                str(root) + "/",
                target + "/",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=child_environment(),
        )
    if result.returncode:
        raise CrigError("Cannot preview this upload. Check file permissions and ignore patterns.")
    console.print(result.stdout or "No files to upload.", markup=False)
    console.print("Preview only. No remote files changed. Filename rules do not detect every secret.")


def default_startup_script() -> str:
    return files("cloudrig").joinpath("bootstrap.sh").read_text(encoding="utf-8")


def load_startup_script(path: str | None, *, root: Path = Path(".")) -> str | None:
    if not path:
        return None
    safe_text(path)
    source = Path(path).expanduser()
    source = (root / source).resolve()
    check_transfer_path(source)
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CrigError(f"Cannot read startup script: {source}") from error


def run_startup(name: str, timeout: float, *, script: str | None = None) -> None:
    if script == "":
        return
    minimum = ".".join(map(str, MIN_RSYNC))
    command = (
        'mkdir -p "$HOME/.cache/cloudrig" && '
        f'bash -se -- {minimum} > "$HOME/.cache/cloudrig/startup.log" 2>&1'
    )
    try:
        result = subprocess.run(
            ssh_command(name, command),
            input=default_startup_script() if script is None else script,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=child_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise CrigError(
            "GPU startup script timed out. Reconnect to retry on the same instance. "
            "Remote log: ~/.cache/cloudrig/startup.log."
        ) from error
    if result.returncode == 77 and script is None:
        raise CrigError("Automatic GPU tool installation needs root SSH. Preinstall rsync 3.5.0+ and uv.")
    if result.returncode:
        raise CrigError("GPU startup script failed. Remote log: ~/.cache/cloudrig/startup.log.")


def check_rsync() -> tuple[int, int, int]:
    result = subprocess.run(
        [require("rsync"), "--version"], capture_output=True, text=True, check=False, env=child_environment()
    )
    match = re.search(r"^rsync\s+version\s+(\d+)\.(\d+)\.(\d+)", result.stdout, re.MULTILINE)
    if result.returncode or not match or tuple(map(int, match.groups())) < MIN_RSYNC:
        raise CrigError(
            "File transfer requires upstream rsync 3.5.0+ locally. macOS: brew install rsync. "
            "Use an updated rsync on the remote host too; legacy compatibility mode is disabled."
        )
    return tuple(map(int, match.groups()))


def check_remote_rsync(name: str) -> None:
    try:
        result = subprocess.run(
            ssh_command(name, "rsync --version"),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=child_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise CrigError("Timed out checking remote rsync. No file transfer was started.") from error
    match = re.search(r"^rsync\s+version\s+(\d+)\.(\d+)\.(\d+)", result.stdout, re.MULTILINE)
    if result.returncode or not match or tuple(map(int, match.groups())) < MIN_RSYNC:
        raise CrigError(
            "Remote file transfer requires upstream rsync 3.5.0+. Update it on the GPU host first."
        )


def sync_command(
    instance: Instance,
    local: str,
    remote: str,
    upload: bool,
    dry_run: bool = False,
) -> list[str]:
    full_ssh(instance)
    if not remote.startswith("/") or any(c in remote for c in "\n\r\x00"):
        raise CrigError("Use an absolute remote path, for example /workspace/project/.")
    check_transfer_path(Path(local).expanduser().resolve())
    name = install_alias(instance)
    check_rsync()
    check_remote_rsync(name)
    endpoint = f"{name}:{remote}"
    transport = shlex.join(ssh_command(name)[:-1])
    argv = ["rsync", "-rlptvhP", "--protect-args", "--safe-links", "--chmod=ugo-s", "-e", transport]
    if dry_run:
        argv.append("--dry-run")
    if upload:
        argv.extend(upload_flags(Path(local).expanduser().resolve()))
    argv.extend(
        ["--", str(Path(local).expanduser().absolute()), endpoint]
        if upload
        else [
            "--",
            endpoint,
            str(Path(local).expanduser().absolute()),
        ]
    )
    # Path.absolute strips trailing '/', which changes rsync directory semantics.
    local_index = -2 if upload else -1
    if local.endswith("/") and not argv[local_index].endswith("/"):
        argv[local_index] += "/"
    return argv
