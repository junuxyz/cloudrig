"""Unprivileged client. It never loads provider tokens or SSH private keys."""

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

from cloudrig.models import CrigError, Instance, Offer, provider_name
from cloudrig.security import child_environment

PUBLIC_ROOT = Path("/var/run/cloudrig")
ROOT_UID = 0
HELPER = Path(
    "/Library/PrivilegedHelperTools/cloudrig-admin"
    if sys.platform == "darwin"
    else "/usr/local/libexec/cloudrig-admin"
)

DEFAULT_IMAGES = {
    "vastai": "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime",
    "runpod": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
}
BILLING_URLS = {
    "vastai": "https://cloud.vast.ai/billing/",
    "runpod": "https://console.runpod.io/user/billing",
}


def trusted_root_path(path: Path) -> None:
    resolved = path.resolve(strict=True)
    for item in (resolved, *resolved.parents):
        info = item.stat()
        if info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise CrigError("The cloudrig helper path is not protected by root ownership.")


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
            "Protected cloudrig service is unavailable. Install it with scripts/install-admin.sh."
        ) from error
    if result.get("error"):
        raise CrigError(str(result["error"]))
    return result.get("data")


def admin(operation: str, *arguments: str):
    if operation not in {"login", "approve", "rent", "start", "destroy", "self-test"}:
        raise CrigError("Unknown privileged operation.")
    try:
        trusted_root_path(HELPER)
    except OSError as error:
        raise CrigError("Install the root-owned cloudrig helper before this operation.") from error
    result = subprocess.run(
        ["/usr/bin/sudo", "-k", "--", str(HELPER), operation, *arguments],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        check=False,
        env=child_environment(),
    )
    if result.returncode:
        raise CrigError(
            "Privileged operation did not complete. Inspect crig --rented before retrying a rental."
        )
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise CrigError("Invalid helper result. Check crig --rented before repeating a rental.") from error


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
        result = admin(
            "rent",
            self.name,
            rental.offer.id,
            rental.offer.gpu,
            rental.name,
            rental.image,
            str(rental.disk),
            str(rental.offer.count),
        )
        return result["id"]

    def approve(self, identifier: str):
        admin("approve", self.name, identifier)

    def stop(self, identifier: str):
        rpc("stop", self.name, id=identifier)

    def start(self, identifier: str):
        admin("start", self.name, identifier)

    def destroy(self, identifier: str):
        admin("destroy", self.name, identifier)
