"""Project requirements and local workspace receipts. No provider credentials live here."""

import hashlib
import json
import re
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from filelock import FileLock, Timeout

from cloudrig import state
from cloudrig.models import CrigError, Offer, number, safe_text
from cloudrig.security import check_transfer_path, private_directory, regular_file


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    provider: str = "vastai"
    gpu: str = ""
    vram: float = 24
    count: int = 1
    disk: int = 40
    max_hourly: float = 1
    hours: float = 4
    region: str = ""
    image: str = ""

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.provider}:{self.root}".encode()).hexdigest()[:16]

    @property
    def remote(self) -> str:
        return f"/workspace/{self.name}-{self.key[:6]}"

    @property
    def rental_name(self) -> str:
        return f"{self.name[:40]}-{self.key[:6]}"

    @property
    def setup(self) -> list[str]:
        if (self.root / "uv.lock").is_file():
            return ["uv", "sync", "--frozen"]
        if (self.root / "requirements.txt").is_file():
            return ["python", "-m", "pip", "install", "-r", "requirements.txt"]
        return []

    def to_dict(self) -> dict:
        return {**asdict(self), "root": str(self.root), "remote": self.remote, "setup": self.setup}


SETTINGS = ("provider", "gpu", "vram", "count", "disk", "max_hourly", "hours", "region", "image")


def load_project(path: str, *, default_provider: str = "vastai", **overrides) -> Project:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise CrigError("Choose an existing project directory.")
    check_transfer_path(root)
    manifest = root / "crig.toml"
    values = {"provider": default_provider}
    if manifest.exists():
        try:
            configured = tomllib.loads(manifest.read_text()).get("workspace", {})
        except (OSError, ValueError) as error:
            raise CrigError("Cannot read crig.toml. Check its TOML syntax.") from error
        if not isinstance(configured, dict) or configured.keys() - set(SETTINGS):
            raise CrigError("Unknown workspace settings in crig.toml. See crig init --help.")
        values.update(configured)
    values.update({key: value for key, value in overrides.items() if value is not None})
    name = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_")[:40] or "project"
    project = Project(root, name, **values)
    if project.provider not in state.PROVIDERS:
        raise CrigError("Workspace provider must be vastai or runpod.")
    for field in ("vram", "max_hourly", "hours"):
        number(getattr(project, field), positive=True)
    for field in ("count", "disk"):
        value = getattr(project, field)
        limit = 64 if field == "count" else 100000
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise CrigError(f"Workspace {field} must be a positive integer.")
    for field in ("gpu", "region", "image"):
        safe_text(getattr(project, field))
    if any(c.isspace() for c in project.image):
        raise CrigError("Use a container image without spaces.")
    return project


def candidates(project: Project, offers: list[Offer]) -> list[Offer]:
    """Require a complete quote for a price ceiling. Never guess an unknown storage rate."""
    rows = [
        row
        for row in offers
        if row.provider == project.provider
        and project.gpu.casefold() in row.gpu.casefold()
        and row.vram >= project.vram
        and row.count == project.count
        and (row.disk_max is None or row.disk_max >= project.disk)
        and project.region.casefold() in row.region.casefold()
        and row.availability == "available"
        and row.total(project.disk) is not None
        and row.total(project.disk) <= project.max_hourly
    ]
    return sorted(rows, key=lambda row: (row.total(project.disk), -(row.reliability or 0), row.id))


class Phase(StrEnum):
    SUBMITTING = "submitting"
    PREPARING = "preparing"
    UPLOADED = "uploaded"
    READY = "ready"


@dataclass(frozen=True)
class Binding:
    phase: Phase
    id: str | None = None
    offer: str | None = None
    python: str = "python"
    setup_complete: bool = False

    @property
    def needs_upload(self) -> bool:
        return self.phase == Phase.PREPARING


def receipt(project: Project) -> Binding | None:
    saved = state.load().get("projects", {}).get(project.key)
    if saved is None:
        return None
    try:
        return Binding(
            phase=Phase(saved["phase"]),
            id=saved.get("id"),
            offer=saved.get("offer"),
            python=saved.get("python", "python"),
            setup_complete=saved.get("setup_complete", False),
        )
    except (TypeError, KeyError, ValueError) as error:
        raise CrigError(
            "Invalid project binding. Inspect crig --rented before repairing local state."
        ) from error


def save_receipt(project: Project, binding: Binding) -> Binding:
    with state.state_lock():
        data = state.load()
        data.setdefault("projects", {})[project.key] = asdict(binding)
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
    return binding


def forget_receipt(project: Project) -> None:
    with state.state_lock():
        data = state.load()
        data.get("projects", {}).pop(project.key, None)
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")


@contextmanager
def project_lock(project: Project):
    root = state.config_dir()
    private_directory(root)
    path = root / f"project-{project.key}.lock"
    regular_file(path)
    try:
        with FileLock(path, timeout=0, mode=0o600):
            yield
    except Timeout as error:
        raise CrigError("Another command is using this project. Wait for it to finish.") from error
