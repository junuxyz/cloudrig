import ipaddress
import re
import shutil
import subprocess
import time
from pathlib import Path

from filelock import FileLock

from cloudrig import broker
from cloudrig.models import CrigError, Instance, RateLimited, safe_text
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
            f"Authorize this GPU first: crig {instance.id} code. This needs fresh sudo authentication."
        )
    if not all((instance.public_key, instance.agent_socket, instance.known_hosts)):
        raise CrigError(f"SSH setup is still in progress. Reconnect with crig {instance.id} code.")
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


def full_ssh(instance: Instance) -> None:
    if not instance.direct:
        raise CrigError("This pod exposes proxy SSH only. Expose 22/tcp for VS Code.")


def open_code(instance: Instance, remote_path: str) -> None:
    full_ssh(instance)
    name = install_alias(instance)
    if not remote_path.startswith("/") or any(ord(c) < 32 for c in remote_path):
        raise CrigError("Use an absolute remote workspace path, for example /workspace/project.")
    run(["code", "--remote", f"ssh-remote+{name}", remote_path])


def connect_editor(api, identifier: str, timeout: float = 300, progress=None) -> None:
    require("code")
    require("ssh")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if progress:
            progress("Waiting for the editor connection")
        try:
            item = api.instance(identifier)
        except RateLimited as error:
            if progress:
                progress(str(error))
            time.sleep(min(error.seconds, max(0, deadline - time.monotonic())))
            continue
        if item.status in {"stopped", "exited", "failed", "error", "terminated"}:
            raise CrigError(f"Instance {identifier} is {item.status}. Choose a running GPU.")
        if not item.approved and item.status == "running" and item.host and item.port:
            api.approve(identifier)
            continue
        if item.host and item.port and item.agent_socket and Path(item.agent_socket).is_socket():
            open_code(item, "/workspace")
            return
        time.sleep(5)
    raise CrigError(
        f"Editor connection is not ready. GPU {identifier} is still rented. Retry crig {identifier} code."
    )
