"""Load project settings and save allocation records."""

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
    startup: str = ""
    framework: str = "none"

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


SETTINGS = (
    "provider",
    "gpu",
    "vram",
    "count",
    "disk",
    "max_hourly",
    "hours",
    "region",
    "image",
    "startup",
    "framework",
)


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
        if not isinstance(configured, dict):
            raise CrigError("Use a [workspace] table in crig.toml.")
        if unknown := configured.keys() - set(SETTINGS):
            names = ", ".join(repr(key) for key in sorted(unknown))
            raise CrigError(f"Unknown settings in crig.toml: {names}.")
        values.update(configured)
    values.update({key: value for key, value in overrides.items() if value is not None})
    name = re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-_")[:40] or "project"
    project = Project(root, name, **values)
    if project.provider not in state.PROVIDERS:
        raise CrigError("Workspace provider must be vastai or runpod.")
    if project.framework not in ("none", "pytorch"):
        raise CrigError("Workspace framework must be none or pytorch.")
    for field in ("vram", "max_hourly", "hours"):
        number(getattr(project, field), positive=True)
    for field in ("count", "disk"):
        value = getattr(project, field)
        limit = 64 if field == "count" else 100000
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise CrigError(f"Workspace {field} must be a positive integer.")
    for field in ("gpu", "region", "image", "startup"):
        safe_text(getattr(project, field))
    if any(c.isspace() for c in project.image):
        raise CrigError("Use a container image without spaces.")
    return project


def candidates(project: Project, offers: list[Offer]) -> list[Offer]:
    """Match the GPU requirements and compute-plus-disk price limit."""
    matches = []
    for offer in offers:
        if (
            offer.provider != project.provider
            or project.gpu.casefold() not in offer.gpu.casefold()
            or offer.vram < project.vram
            or offer.count != project.count
            or (offer.disk_max is not None and offer.disk_max < project.disk)
            or project.region.casefold() not in offer.region.casefold()
            or offer.availability != "available"
        ):
            continue
        price = offer.total(project.disk)
        if price is not None and price <= project.max_hourly:
            matches.append((price, offer))
    matches.sort(key=lambda item: (item[0], -(item[1].reliability or 0), item[1].id))
    return [offer for _, offer in matches]


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
        data.setdefault("projects", {})[project.key] = {
            **asdict(binding),
            "root": str(project.root),
            "provider": project.provider,
        }
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
    return binding


def forget_receipt(project: Project) -> None:
    with state.state_lock():
        data = state.load()
        data.get("projects", {}).pop(project.key, None)
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")


def bound_provider(root: Path) -> str | None:
    """Find an allocation independently of the current provider preference."""
    projects = state.load().get("projects", {})
    providers = [
        provider
        for provider in state.PROVIDERS
        if Project(root, root.name, provider=provider).key in projects
    ]
    if len(providers) > 1:
        raise CrigError("This project has bindings on both providers. Select one with --provider.")
    return providers[0] if providers else None


@contextmanager
def project_lock(project: Project, *, timeout: float = 0):
    root = state.config_dir()
    private_directory(root)
    path = root / f"project-{project.key}.lock"
    regular_file(path)
    try:
        with FileLock(path, timeout=timeout, mode=0o600):
            yield
    except Timeout as error:
        raise CrigError("Another command is using this project. Wait for it to finish.") from error
