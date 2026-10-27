import ipaddress
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock

from cloudrig import broker
from cloudrig.models import CrigError, Instance, safe_text
from cloudrig.security import child_environment, private_directory, regular_file
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
    suffix = instance.id
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", suffix):
        raise CrigError("Provider returned an invalid instance ID.")
    return f"crig-{instance.provider}-{suffix}"


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
    if not instance.approved or not all((instance.public_key, instance.agent_socket, instance.known_hosts)):
        raise CrigError(
            f"Authorize this GPU first: crig {instance.id} approve. This needs fresh sudo authentication."
        )
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


def install_alias(instance: Instance, identity: Path | None = None) -> str:
    validate_endpoint(instance)
    require("ssh")
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


def wait_ready(provider, identifier: str, identity: Path | None = None, timeout: float = 300) -> Instance:
    deadline = time.monotonic() + timeout
    while True:
        item = provider.instance(identifier)
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
            name = install_alias(item, identity)
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
                return item
        if time.monotonic() >= deadline:
            raise CrigError(
                f"Instance {identifier} is still rented; SSH is not ready. "
                f"Reconnect with crig {identifier} ssh. No second instance was created."
            )
        time.sleep(3)


def full_ssh(instance: Instance) -> None:
    if not instance.direct:
        raise CrigError("This pod exposes proxy SSH only. Expose 22/tcp for VS Code, commands, or rsync.")


def open_code(instance: Instance, identity: Path, remote_path: str) -> None:
    full_ssh(instance)
    name = install_alias(instance, identity)
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
)


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
    identity: Path,
    local: str,
    remote: str,
    upload: bool,
    dry_run: bool = False,
) -> list[str]:
    full_ssh(instance)
    if not remote.startswith("/") or any(c in remote for c in "\n\r\x00"):
        raise CrigError("Use an absolute remote path, for example /workspace/project/.")
    local_path = Path(local).expanduser().resolve()
    home = Path.home().resolve()
    protected_roots = [home / name for name in (".ssh", ".aws", ".azure", ".kube", ".gnupg", ".config")]
    sensitive_names = {
        ".bashrc",
        ".bash_profile",
        ".zshrc",
        ".zprofile",
        ".profile",
        ".gitconfig",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
    }
    if (
        local_path in {home, Path("/")}
        or local_path.name in sensitive_names
        or any(local_path == root or root in local_path.parents for root in protected_roots)
    ):
        raise CrigError(
            "Refusing a home, credential, or shell-configuration path. Choose a project directory."
        )
    name = install_alias(instance, identity)
    require("rsync")
    check_rsync()
    check_remote_rsync(name)
    endpoint = f"{name}:{remote}"
    transport = shlex.join(ssh_command(name)[:-1])
    argv = ["rsync", "-rlptvhP", "--protect-args", "--safe-links", "--chmod=ugo-s", "-e", transport]
    if dry_run:
        argv.append("--dry-run")
    if upload:
        argv.extend(f"--exclude={pattern}" for pattern in SECRET_EXCLUDES)
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


def parse_ssh(value: str, provider: str, identifier: str) -> tuple[Instance, Path | None]:
    """Accept a narrow SSH form. Never execute clipboard contents or preserve arbitrary options."""
    identity = None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CrigError("SSH clipboard input cannot contain control characters.")
    if value.startswith("ssh://"):
        parsed = urlsplit(value.strip())
        if parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise CrigError("SSH URLs must contain only a user, host, and port.")
        try:
            port = parsed.port if parsed.port is not None else 22
        except ValueError as error:
            raise CrigError("Invalid SSH URL port.") from error
        item = Instance(
            identifier,
            provider,
            identifier,
            "GPU",
            1,
            "running",
            0,
            "",
            parsed.hostname,
            port,
            parsed.username or "root",
        )
    else:
        try:
            parts = shlex.split(value.strip())
        except ValueError as error:
            raise CrigError("Malformed quoting in SSH command.") from error
        if not parts or parts.pop(0) != "ssh":
            raise CrigError("Expected ssh user@host -p PORT -i KEY, or an ssh:// URL.")
        user, host, port = "root", None, 22
        while parts:
            part = parts.pop(0)
            if part in {"-p", "-i"}:
                if not parts:
                    raise CrigError(f"Missing value for {part}.")
                arg = parts.pop(0)
                if part == "-p":
                    try:
                        port = int(arg)
                    except ValueError as error:
                        raise CrigError("Invalid SSH port.") from error
                else:
                    identity = Path(arg).expanduser()
            elif not part.startswith("-") and "@" in part and host is None:
                user, host = part.rsplit("@", 1)
                host = host.removeprefix("[").removesuffix("]")
            else:
                raise CrigError("Unsupported SSH option or trailing command. Use user@host -p PORT -i KEY.")
        item = Instance(identifier, provider, identifier, "GPU", 1, "running", 0, "", host, port, user)
    validate_endpoint(item)
    return item, identity


def clipboard() -> str:
    for argv in (["pbpaste"], ["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
        if shutil.which(argv[0]):
            result = subprocess.run(
                argv, capture_output=True, text=True, check=False, env=child_environment()
            )
            if result.returncode == 0:
                return result.stdout.strip()
    raise CrigError("No clipboard reader available. Install wl-paste or xclip on Linux.")


def with_connection(provider, instance: Instance) -> Instance:
    return provider.instance(instance.id)
