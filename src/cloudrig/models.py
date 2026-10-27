import math
import re
import unicodedata
from dataclasses import asdict, dataclass


class CrigError(Exception):
    """An actionable error safe to display without a traceback."""


def safe_text(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 1024
        or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)
    ):
        raise CrigError("Cloud metadata contains invalid text or terminal control characters.")


def instance_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise CrigError("Provider returned an invalid instance ID.")
    return value


def number(value: float, *, positive: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise CrigError("Provider returned an invalid price, capacity, or quantity. Refresh before renting.")


def provider_name(value: str) -> None:
    if value not in {"vastai", "runpod"}:
        raise CrigError("Unknown provider.")


@dataclass(frozen=True)
class Offer:
    id: str
    provider: str
    gpu: str
    count: int
    vram: float
    hourly: float
    region: str
    availability: str = "available"
    cloud: str = ""
    disk_hourly_gb: float | None = None
    disk_max: float | None = None
    reliability: float | None = None

    def __post_init__(self):
        provider_name(self.provider)
        for value in (self.id, self.gpu, self.region, self.cloud, self.availability):
            safe_text(value)
        if self.provider == "vastai":
            instance_id(self.id)
        elif len(self.id.split("|")) != 3:
            raise CrigError("Invalid RunPod offer identifier.")
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            raise CrigError("Invalid GPU count.")
        number(self.vram, positive=True)
        number(self.hourly)
        for value in (self.disk_hourly_gb, self.disk_max, self.reliability):
            if value is not None:
                number(value)
        if self.reliability is not None and self.reliability > 1:
            raise CrigError("Invalid reliability value.")

    @property
    def title(self) -> str:
        return f"{self.gpu} ×{self.count}" if self.count > 1 else self.gpu

    def total(self, disk: int) -> float | None:
        if self.disk_hourly_gb is None:
            return None
        total = self.hourly + disk * self.disk_hourly_gb
        number(total)
        return total

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Instance:
    id: str
    provider: str
    name: str
    gpu: str
    count: int
    status: str
    hourly: float
    region: str
    host: str | None = None
    port: int | None = None
    user: str = "root"
    direct: bool = True
    public_key: str | None = None
    agent_socket: str | None = None
    known_hosts: str | None = None
    approved: bool = False

    def __post_init__(self):
        instance_id(self.id)
        provider_name(self.provider)
        for value in (self.name, self.gpu, self.region, self.status):
            safe_text(value)
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            raise CrigError("Invalid GPU count.")
        number(self.hourly)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Rental:
    offer: Offer
    name: str
    disk: int
    image: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", self.name):
            raise CrigError("Invalid workspace name.")
        if isinstance(self.disk, bool) or not isinstance(self.disk, int) or not 1 <= self.disk <= 100000:
            raise CrigError("Invalid container disk size.")
        safe_text(self.image)
        if not self.image or any(c.isspace() for c in self.image):
            raise CrigError("Invalid container image.")


@dataclass(frozen=True)
class InstanceAction:
    instance: Instance
    action: str
