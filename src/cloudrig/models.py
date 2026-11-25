import math
import re
import unicodedata
from dataclasses import asdict, dataclass


class CrigError(Exception):
    """An error safe to display to the user."""


class Cancelled(CrigError):
    """The user cancelled an action before it was submitted."""


class RateLimited(CrigError):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Vast.ai request limit reached. Try again in {seconds}s.")


def safe_text(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 1024
        or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)
    ):
        raise CrigError("Use text with at most 1024 characters and no terminal control characters.")


def instance_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise CrigError("Invalid instance ID. Use up to 100 letters, numbers, hyphens, or underscores.")
    return value


def number(value: float, *, positive: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        limit = "greater than zero" if positive else "zero or greater"
        raise CrigError(f"Use a finite number {limit}.")


@dataclass(frozen=True)
class Offer:
    id: str
    provider: str
    gpu: str
    count: int
    vram: float
    hourly: float
    region: str
    disk_hourly_gb: float | None = None
    disk_max: float | None = None
    reliability: float | None = None

    def __post_init__(self):
        if self.provider != "vastai":
            raise CrigError("Only Vast.ai is supported.")
        instance_id(self.id)
        for value in (self.gpu, self.region):
            safe_text(value)
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            raise CrigError("Invalid GPU count.")
        number(self.vram, positive=True)
        number(self.hourly)
        for value in (self.disk_hourly_gb, self.disk_max, self.reliability):
            if value is not None:
                number(value)
        if self.reliability is not None and self.reliability > 1:
            raise CrigError("Invalid reliability value.")

    def total(self, disk: int) -> float | None:
        if self.disk_hourly_gb is None:
            return None
        total = self.hourly + disk * self.disk_hourly_gb
        number(total)
        return total

    def to_dict(self) -> dict:
        return asdict(self)


def offer_hourly(offer: Offer, disk: int) -> float:
    total = offer.total(disk)
    return total if total is not None else offer.hourly


def sorted_offers(offers: list[Offer], sort: str, disk: int) -> list[Offer]:
    def key(offer: Offer):
        price = offer_hourly(offer, disk)
        order = {"price": price, "vram": -offer.vram, "region": offer.region, "gpus": -offer.count}
        return order[sort], price, offer.id

    return sorted(offers, key=key)


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
    proxy_host: str | None = None
    proxy_port: int | None = None

    def __post_init__(self):
        instance_id(self.id)
        if self.provider != "vastai":
            raise CrigError("Only Vast.ai is supported.")
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
            raise CrigError(
                "Start the name with a letter or number. "
                "Use up to 63 letters, numbers, hyphens, or underscores."
            )
        if isinstance(self.disk, bool) or not isinstance(self.disk, int) or not 1 <= self.disk <= 100000:
            raise CrigError("Choose a disk size between 1 and 100000 GB.")
        safe_text(self.image)
        if not self.image or any(c.isspace() for c in self.image):
            raise CrigError("Enter a container image without spaces.")


@dataclass(frozen=True)
class ImageTemplate:
    name: str
    image: str
    disk: int | None = None

    def __post_init__(self):
        safe_text(self.name)
        safe_text(self.image)
        if not self.name.strip():
            raise CrigError("Template name cannot be empty.")
        if not self.image or any(c.isspace() for c in self.image):
            raise CrigError("Enter a container image without spaces.")


@dataclass(frozen=True)
class InstanceAction:
    instance: Instance
    action: str
