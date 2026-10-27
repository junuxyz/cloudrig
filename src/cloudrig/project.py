"""Project requirements and local workspace receipts. No provider credentials live here."""

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from cloudrig import state
from cloudrig.models import CrigError, Offer, number, safe_text


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


FIELDS = {"provider", "gpu", "vram", "count", "disk", "max_hourly", "hours", "region", "image"}


def load_project(path: str, **overrides) -> Project:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise CrigError("Choose an existing project directory.")
    if root in {Path.home().resolve(), Path("/")}:
        raise CrigError("Choose a project directory, not your home or filesystem root.")
    protected = [
        Path.home().resolve() / name for name in (".ssh", ".aws", ".azure", ".kube", ".gnupg", ".config")
    ]
    if any(root == path or path in root.parents for path in protected):
        raise CrigError("Choose a project directory outside your credential folders.")
    manifest = root / "crig.toml"
    values = {}
    if manifest.exists():
        try:
            values = tomllib.loads(manifest.read_text()).get("workspace", {})
        except (OSError, ValueError) as error:
            raise CrigError("Cannot read crig.toml. Check its TOML syntax.") from error
        if not isinstance(values, dict) or values.keys() - FIELDS:
            raise CrigError("Unknown workspace settings in crig.toml. See crig init --help.")
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


def receipt(project: Project) -> dict | None:
    return state.load().get("projects", {}).get(project.key)


def save_receipt(project: Project, **changes) -> dict:
    with state.state_lock():
        data = state.load()
        projects = data.setdefault("projects", {})
        item = projects.setdefault(project.key, {})
        item.update(root=str(project.root), provider=project.provider, remote=project.remote, **changes)
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")
        return dict(item)


def forget_receipt(project: Project) -> None:
    with state.state_lock():
        data = state.load()
        data.get("projects", {}).pop(project.key, None)
        state.atomic_write(state.config_dir() / "config.json", json.dumps(data, indent=2) + "\n")


def with_default_image(project: Project, image: str) -> Project:
    return replace(project, image=project.image or image)
