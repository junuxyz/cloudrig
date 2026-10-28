"""Check official releases without delaying the dashboard."""

import json
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cloudrig import __version__, state
from cloudrig.models import CrigError

REPOSITORY = "junuxyz/cloudrig"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = 10 * 24 * 60 * 60


def version_number(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable release version")
    return tuple(map(int, version.split(".")))


@dataclass(frozen=True)
class Release:
    version: str
    assets: frozenset[str]


def latest_release() -> Release | None:
    request = Request(API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "cloudrig"})
    try:
        with urlopen(request, timeout=5) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Release response is too large")
        data = json.loads(payload)
        if data["draft"] is not False or data["prerelease"] is not False:
            raise ValueError("Expected a published stable release")
        tag = data["tag_name"]
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ValueError("Invalid release tag")
        version_number(tag[1:])
        assets = data["assets"]
        if not isinstance(assets, list) or any(not isinstance(row.get("name"), str) for row in assets):
            raise ValueError("Invalid release assets")
        return Release(tag[1:], frozenset(row["name"] for row in assets))
    except HTTPError as error:
        if error.code == 404:
            return None
        raise CrigError("Could not check GitHub releases. Try crig update later.") from error
    except (OSError, URLError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise CrigError("Could not check GitHub releases. Try crig update later.") from error


def check_notice() -> str | None:
    """Claim one check every ten days across processes. All failures stay silent."""
    try:
        path = state.config_dir() / "updates.json"
        now = time.time()
        with state.state_lock():
            try:
                previous = json.loads(path.read_text())["checked_at"]
            except (OSError, ValueError, KeyError, TypeError):
                previous = 0
            if isinstance(previous, (int, float)) and 0 <= now - previous < CHECK_INTERVAL:
                return None
            state.atomic_write(path, json.dumps({"checked_at": now}) + "\n")
        release = latest_release()
        if release and version_number(release.version) > version_number(__version__):
            return release.version
    except (OSError, CrigError, ValueError):
        pass
    return None
