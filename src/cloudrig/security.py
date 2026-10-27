"""Boundaries between local secrets and untrusted cloud machines."""

import os
import stat
from pathlib import Path

from cloudrig.models import CrigError

# Child tools do not need provider, model, registry, or cloud credentials. An allowlist also
# removes inherited RSYNC_RSH, SSH_ASKPASS, BASH_ENV, LD_PRELOAD, and DYLD_* hooks.
CHILD_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "TMPDIR",
        "TMP",
        "TEMP",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "TERM_PROGRAM",
        "TERM_PROGRAM_VERSION",
        "VSCODE_IPC_HOOK_CLI",
        "__CF_USER_TEXT_ENCODING",
        "SystemRoot",
        "WINDIR",
        "PATHEXT",
    }
)


def child_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name in CHILD_ENV}


def check_transfer_path(path: Path) -> None:
    home = Path.home().resolve()
    credential_dirs = [home / name for name in (".ssh", ".aws", ".azure", ".kube", ".gnupg", ".config")]
    config_files = {
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
        path in {home, Path("/")}
        or path.name in config_files
        or any(path == root or root in path.parents for root in credential_dirs)
    ):
        raise CrigError(
            "Refusing a home, credential, or shell-configuration path. Choose a project directory."
        )


def private_directory(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CrigError(f"Expected an owned private directory at {path}. Use a real directory with mode 700.")


def regular_file(path: Path, *, private: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & forbidden:
        raise CrigError(
            f"Refusing unsafe file {path}. Use an owned regular file with restricted permissions."
        )
