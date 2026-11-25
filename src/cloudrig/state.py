"""Local preferences and safe file helpers."""

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from cloudrig.models import CrigError

_CHILD_ENV = frozenset(
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
        "TZ",
        "TMPDIR",
        "TMP",
        "TEMP",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "TERM_PROGRAM",
        "VSCODE_IPC_HOOK_CLI",
    }
)


def child_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name in _CHILD_ENV}


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "cloudrig"


def private_dir(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CrigError(f"Expected a directory owned by you at {path}.")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    private_dir(path.parent)
    if path.is_symlink():
        raise CrigError(f"Refusing to overwrite symlink {path}.")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def state_lock():
    root = config_dir()
    private_dir(root)
    with FileLock(root / ".lock", timeout=10, mode=0o600):
        yield


def load() -> dict:
    path = config_dir() / "config.json"
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError) as error:
        raise CrigError(f"Cannot read {path}. Fix or remove it and retry.") from error


def update(**changes) -> dict:
    with state_lock():
        data = load()
        data.update(changes)
        atomic_write(config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
        return data
