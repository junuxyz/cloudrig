"""Per-instance throwaway SSH keys and VS Code Remote-SSH connections."""

import ipaddress
import math
import re
import secrets
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from filelock import FileLock

from cloudrig.models import CrigError, Instance, RateLimited, instance_id, safe_text
from cloudrig.state import atomic_write, child_environment, private_dir
from cloudrig.terminal import run_quietly


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        if binary == "code":
            raise CrigError(
                "Install VS Code, then open its Command Palette and choose "
                "Shell Command: Install 'code' command in PATH."
            )
        raise CrigError(f"{binary} is not installed or on PATH.")
    return path


def managed_dir() -> Path:
    ssh_root = Path.home() / ".ssh"
    private_dir(ssh_root)
    folder = ssh_root / "cloudrig"
    private_dir(folder)
    return folder


def key_path(ident: str) -> Path:
    instance_id(ident)
    return managed_dir() / f"id_{ident}"


def known_hosts_path(ident: str) -> Path:
    instance_id(ident)
    return managed_dir() / f"known_hosts_{ident}"


def alias(ident: str) -> str:
    instance_id(ident)
    return f"crig-{ident}"


def has_keypair(ident: str) -> bool:
    private = key_path(ident)
    return private.is_file() and private.with_suffix(".pub").is_file()


def create_temp_keypair() -> tuple[Path, str]:
    """Generate a throwaway ed25519 keypair before an instance ID is assigned."""
    private = managed_dir() / f".pending_{secrets.token_hex(8)}"
    public = private.with_suffix(".pub")
    result = run_quietly(
        [require("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", "cloudrig", "-f", str(private)]
    )
    if result.returncode:
        private.unlink(missing_ok=True)
        public.unlink(missing_ok=True)
        raise CrigError("Could not generate an SSH key for this GPU.")
    private.chmod(0o600)
    pub = public.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+ [A-Za-z0-9_.-]+", pub):
        private.unlink(missing_ok=True)
        public.unlink(missing_ok=True)
        raise CrigError("Generated SSH public key had an unexpected format.")
    return private, pub


def bind_temp_keypair(temp_private: Path, ident: str) -> Path:
    """Rename a pending keypair to its assigned instance ID."""
    target = key_path(ident)
    temp_private.replace(target)
    temp_private.with_suffix(".pub").replace(target.with_suffix(".pub"))
    target.chmod(0o600)
    return target


def discard_temp_keypair(temp_private: Path) -> None:
    temp_private.unlink(missing_ok=True)
    temp_private.with_suffix(".pub").unlink(missing_ok=True)


def ensure_keypair(ident: str) -> tuple[Path, str]:
    private = key_path(ident)
    public = private.with_suffix(".pub")
    if not (private.is_file() and public.is_file()):
        private.unlink(missing_ok=True)
        public.unlink(missing_ok=True)
        result = run_quietly(
            [
                require("ssh-keygen"),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"cloudrig-{ident}",
                "-f",
                str(private),
            ]
        )
        if result.returncode:
            raise CrigError("Could not generate an SSH key for this GPU.")
        private.chmod(0o600)
    return private, public.read_text(encoding="utf-8").strip()


def _validate_endpoint(host: str, port: int, user: str) -> None:
    safe_text(host)
    safe_text(user)
    if "%" in host:
        raise CrigError("Scoped IPv6 addresses are not supported.")
    try:
        ipaddress.ip_address(host)
        valid_host = True
    except ValueError:
        valid_host = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host))
    if not valid_host or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
        raise CrigError("Vast.ai returned an invalid SSH hostname or user.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise CrigError("SSH port is not ready yet.")


def _ssh_quote(value: str) -> str:
    if any(ch in value for ch in "\n\r\x00"):
        raise CrigError("SSH paths cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def install_alias(ident: str, host: str, port: int, user: str = "root") -> str:
    _validate_endpoint(host, port, user)
    private, _ = ensure_keypair(ident)
    known = known_hosts_path(ident)
    name = alias(ident)
    folder = managed_dir()

    block = (
        f"Host {name}\n"
        f"  HostName {host}\n"
        f"  User {user}\n"
        f"  Port {port}\n"
        f"  IdentityFile {_ssh_quote(str(private))}\n"
        "  IdentitiesOnly yes\n"
        "  ForwardAgent no\n"
        "  ForwardX11 no\n"
        "  PermitLocalCommand no\n"
        "  PasswordAuthentication no\n"
        "  KbdInteractiveAuthentication no\n"
        "  ConnectTimeout 10\n"
        "  ServerAliveInterval 30\n"
        "  ServerAliveCountMax 3\n"
        "  StrictHostKeyChecking accept-new\n"
        f"  HostKeyAlias {name}\n"
        f"  UserKnownHostsFile {_ssh_quote(str(known))}\n"
    )
    with FileLock(folder / ".lock", timeout=10, mode=0o600):
        config = folder / "config"
        contents = config.read_text(encoding="utf-8") if config.exists() else ""
        blocks = re.split(r"(?=^Host )", contents, flags=re.MULTILINE)
        kept = "".join(part for part in blocks if part.splitlines()[:1] != [f"Host {name}"])
        merged = kept.rstrip() + "\n\n" + block if kept.strip() else block
        atomic_write(config, merged, mode=0o600)

        main = Path.home() / ".ssh" / "config"
        original = main.read_text(encoding="utf-8") if main.exists() else ""
        include = f"Include {_ssh_quote(str(config))}"
        if include not in original.splitlines():
            updated = include + "\n\n" + original if original else include + "\n"
            atomic_write(main, updated, mode=0o600)
    return name


def cleanup_instance(ident: str) -> None:
    try:
        folder = managed_dir()
    except CrigError:
        return
    name = alias(ident)
    key_path(ident).unlink(missing_ok=True)
    key_path(ident).with_suffix(".pub").unlink(missing_ok=True)
    known_hosts_path(ident).unlink(missing_ok=True)
    config = folder / "config"
    if not config.exists():
        return
    with FileLock(folder / ".lock", timeout=10, mode=0o600):
        contents = config.read_text(encoding="utf-8")
        blocks = re.split(r"(?=^Host )", contents, flags=re.MULTILINE)
        kept = "".join(part for part in blocks if part.splitlines()[:1] != [f"Host {name}"])
        atomic_write(config, kept.rstrip() + "\n" if kept.strip() else "", mode=0o600)


def _endpoints(item: Instance) -> list[tuple[str, int, bool]]:
    result: list[tuple[str, int, bool]] = []
    if item.host and item.port:
        result.append((item.host, item.port, item.direct))
    if item.proxy_host and item.proxy_port and (item.proxy_host, item.proxy_port) != (item.host, item.port):
        result.append((item.proxy_host, item.proxy_port, False))
    return result


def _tcp_ssh_banner(host: str, port: int, timeout: float = 2.0) -> bool:
    """Fast TCP check: returns True only once sshd actually sends its SSH-2.0 banner."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            banner = sock.recv(64)
            return banner.startswith(b"SSH-")
    except OSError:
        return False


def _probe_ssh(ident: str, host: str, port: int, user: str, timeout: float) -> tuple[bool, bool]:
    """Return (port_open, auth_ok) for one endpoint without touching ~/.ssh/cloudrig/config."""
    if not _tcp_ssh_banner(host, port, timeout=min(2.0, timeout)):
        return False, False
    private, _ = ensure_keypair(ident)
    known = known_hosts_path(ident)
    name = alias(ident)
    try:
        result = subprocess.run(
            [
                require("ssh"),
                "-F",
                "/dev/null",
                "-i",
                str(private),
                "-p",
                str(port),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                f"HostKeyAlias={name}",
                "-o",
                f"UserKnownHostsFile={known}",
                "-o",
                f"ConnectTimeout={max(1, math.ceil(timeout))}",
                "-T",
                f"{user}@{host}",
                "true",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=child_environment(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return True, False
    if result.returncode == 0:
        return True, True
    if "remote host identification has changed" in result.stderr.lower():
        raise CrigError("SSH host key changed for this GPU. Inspect it before reconnecting.")
    return True, False


def ssh_ready(ident: str, host: str, port: int, user: str, timeout: float) -> bool:
    _, auth_ok = _probe_ssh(ident, host, port, user, timeout)
    return auth_ok


def open_code(ident: str, remote_path: str = "/workspace") -> None:
    if not remote_path.startswith("/") or any(ord(c) < 32 for c in remote_path):
        raise CrigError("Use an absolute remote workspace path, for example /workspace.")
    result = run_quietly([require("code"), "--remote", f"ssh-remote+{alias(ident)}", remote_path])
    if result.returncode:
        raise CrigError(f"code exited with status {result.returncode}.")


def connect_editor(api, identifier: str, timeout: float = 300, progress=None) -> None:
    require("code")
    require("ssh")
    deadline = time.monotonic() + timeout
    stage = "Checking GPU status"
    if progress:
        progress(stage)

    attached = has_keypair(identifier)
    cached_item: Instance | None = None
    last_refresh = 0.0

    while time.monotonic() < deadline:
        now = time.monotonic()
        # Once running with known endpoints, avoid paying a ~0.8s Vast.ai API round-trip every poll.
        needs_refresh = (
            cached_item is None
            or cached_item.status != "running"
            or not _endpoints(cached_item)
            or now - last_refresh >= 15
        )
        if needs_refresh:
            try:
                cached_item = api.instance(identifier)
                last_refresh = time.monotonic()
            except RateLimited as error:
                stage = f"Vast.ai request limit reached. Checking again in {error.seconds}s"
                if progress:
                    progress(stage)
                time.sleep(min(error.seconds, max(0, deadline - time.monotonic())))
                continue

        item = cached_item
        if item.status in {"stopped", "exited", "failed", "error", "terminated"}:
            raise CrigError(f"Instance {identifier} is {item.status}. Choose a running GPU.")
        if item.status != "running":
            stage = f"GPU is {item.status}. Waiting for container startup"
            if progress:
                progress(stage)
            time.sleep(min(2.0, max(0, deadline - time.monotonic())))
            continue

        if not attached:
            if progress:
                progress("Attaching SSH key")
            _, pub = ensure_keypair(identifier)
            api.attach_ssh_key(identifier, pub)
            attached = True

        endpoints = _endpoints(item)
        if not endpoints:
            stage = "Waiting for the GPU's SSH address"
            if progress:
                progress(stage)
            time.sleep(min(1.5, max(0, deadline - time.monotonic())))
            continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        probe_timeout = min(4.0, remaining)
        # Probe direct and proxy endpoints concurrently; prefer direct when both succeed.
        with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
            futures = [
                (host, port, direct, pool.submit(ssh_ready, item.id, host, port, item.user, probe_timeout))
                for host, port, direct in endpoints
            ]
            for host, port, direct, fut in futures:
                if fut.result():
                    install_alias(item.id, host, port, item.user)
                    if progress:
                        progress("Opening VS Code" if direct else "Opening VS Code through Vast.ai proxy")
                    open_code(item.id)
                    return

        stage = "Waiting for the GPU to accept SSH login"
        if progress:
            progress(stage)
        time.sleep(min(1.0, max(0, deadline - time.monotonic())))

    raise CrigError(
        f"{stage}. Instance {identifier} is still rented. "
        "Check its status in the Vast.ai console before retrying."
    )
