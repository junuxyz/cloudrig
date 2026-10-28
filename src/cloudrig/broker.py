"""Call the root helper without reading its credentials or private keys."""

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

from cloudrig.models import CrigError, Instance, Offer, RateLimited, RentalNotSubmitted, provider_name
from cloudrig.security import child_environment

ROOT_UID = 0
PROTOCOL_VERSION = 3
HELPER = Path(
    "/Library/PrivilegedHelperTools/cloudrig-admin"
    if sys.platform == "darwin"
    else "/usr/local/libexec/cloudrig-admin"
)
PUBLIC_ROOT = HELPER.parent / "cloudrig" if sys.platform == "darwin" else Path("/var/run/cloudrig")

DEFAULT_IMAGES = {
    "vastai": "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime",
    "runpod": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
}
BILLING_URLS = {
    "vastai": "https://cloud.vast.ai/billing/",
    "runpod": "https://console.runpod.io/user/billing",
}
API_KEY_URLS = {
    "vastai": "https://console.vast.ai/manage-keys/",
    "runpod": "https://console.runpod.io/user/settings",
}


def trusted_root_path(path: Path) -> None:
    resolved = path.resolve(strict=True)
    for item in (resolved, *resolved.parents):
        info = item.stat()
        if info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise CrigError(
                f"Unprotected cloudrig path: {item} "
                f"(uid={info.st_uid}, mode={stat.S_IMODE(info.st_mode):04o}). "
                "Root ownership and no group/world write permission are required."
            )


def rpc(operation: str, provider: str, **values):
    endpoint = PUBLIC_ROOT / "api.sock"
    try:
        trusted_root_path(endpoint.parent)
        info = endpoint.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid not in {ROOT_UID, os.getuid()}:
            raise CrigError("Invalid cloudrig service socket.")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(65)
            client.connect(str(endpoint))
            client.sendall(json.dumps({"op": operation, "provider": provider, **values}).encode() + b"\n")
            with client.makefile("rb") as stream:
                line = stream.readline(32 * 1024 * 1024 + 1)
            if len(line) > 32 * 1024 * 1024:
                raise CrigError("Cloudrig service response is too large.")
        result = json.loads(line)
    except (OSError, ValueError) as error:
        raise CrigError(
            "Cloudrig service is unavailable. Run crig in your own terminal to install or repair it."
        ) from error
    if result.get("error"):
        seconds = result.get("retry_after")
        if type(seconds) is int and 1 <= seconds <= 86400:
            raise RateLimited(seconds)
        raise CrigError(str(result["error"]))
    return result.get("data")


def admin(operation: str, *arguments: str):
    if operation not in {"login", "approve", "rent", "start", "destroy", "recover", "self-test"}:
        raise CrigError("Unknown privileged operation.")
    try:
        trusted_root_path(HELPER)
    except OSError as error:
        raise CrigError("Install the root-owned cloudrig helper before this operation.") from error
    result = subprocess.run(
        ["/usr/bin/sudo", "-k", "--", str(HELPER), operation, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if operation in {"rent", "start", "destroy"} else None,
        stdin=None,
        text=True,
        check=False,
        env=child_environment(),
    )
    if result.returncode:
        for line in reversed((result.stderr or "").splitlines()):
            if line.startswith(("cloudrig-admin:", "sudo:")):
                raise CrigError(line.partition(":")[2].strip())
        raise CrigError(
            f"Authorization or helper execution failed with exit code {result.returncode}. "
            "Inspect crig --rented before retrying a rental."
        )
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise CrigError("Invalid helper result. Check crig --rented before repeating a rental.") from error


def check_service(provider: str) -> None:
    health = rpc("health", provider)
    if not isinstance(health, dict) or health.get("protocol") != PROTOCOL_VERSION:
        raise CrigError("Rebuild and reinstall the Cloudrig helper. Its protocol is outdated.")


class BrokerProvider:
    def __init__(self, name: str):
        provider_name(name)
        self.name = name
        self.default_image = DEFAULT_IMAGES[name]
        self.billing_url = BILLING_URLS[name]

    def offers(self, query: str, disk: int = 40, count: int = 1) -> list[Offer]:
        return [Offer(**row) for row in rpc("offers", self.name, query=query, disk=disk, count=count)]

    def instances(self) -> list[Instance]:
        return [Instance(**row) for row in rpc("instances", self.name)]

    def instance(self, identifier: str) -> Instance:
        return Instance(**rpc("instance", self.name, id=identifier))

    def rent(self, rental) -> str:
        try:
            check_service(self.name)
        except CrigError as error:
            raise RentalNotSubmitted(str(error)) from None
        price = rental.offer.total(rental.disk)
        if price is None:
            price = rental.offer.hourly
        try:
            result = admin(
                "rent",
                self.name,
                rental.offer.id,
                rental.offer.gpu,
                rental.name,
                rental.image,
                str(rental.disk),
                str(rental.offer.count),
                str(price),
            )
        except (CrigError, KeyboardInterrupt) as error:
            try:
                status = rpc("rental-status", self.name, query=rental.name)
            except CrigError:
                raise error from None
            if status["state"] == "absent":
                reason = str(error) if isinstance(error, CrigError) else "Rental request was interrupted."
                raise RentalNotSubmitted(f"{reason}\nNo rental was submitted.") from None
            raise error from None
        return result["id"]

    def recover(self, name: str, identifier: str):
        return admin("recover", self.name, name, identifier)

    def approve(self, identifier: str):
        admin("approve", self.name, identifier)

    def stop(self, identifier: str):
        rpc("stop", self.name, id=identifier)

    def start(self, identifier: str):
        admin("start", self.name, identifier)

    def destroy(self, identifier: str):
        admin("destroy", self.name, identifier)
