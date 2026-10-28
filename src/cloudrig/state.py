import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from cloudrig.models import CrigError
from cloudrig.security import private_directory, regular_file

PROVIDERS = ("vastai", "runpod")


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "cloudrig"


def atomic_write(path: Path, text: str) -> None:
    private_directory(path.parent)
    regular_file(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
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
    private_directory(root)
    regular_file(root / ".lock")
    with FileLock(root / ".lock", timeout=10, mode=0o600):
        yield


def load() -> dict:
    path = config_dir() / "config.json"
    if not path.exists() and not path.is_symlink():
        return {}
    private_directory(path.parent)
    regular_file(path, private=True)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, OSError) as error:
        raise CrigError(
            f"Cannot read {path}. Repair or move this file, then run crig --set vastai."
        ) from error


def update(**changes) -> dict:
    with state_lock():
        data = load()
        data.update(changes)
        atomic_write(config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
        return data


def remember_instance(provider: str, identifier: str) -> None:
    with state_lock():
        data = load()
        active = data.setdefault("active", {})
        active[provider] = identifier
        atomic_write(config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
