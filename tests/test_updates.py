import io
import json
from urllib.error import HTTPError, URLError

import pytest

from cloudrig import state, updates
from cloudrig.models import CrigError


def test_release_checks_use_official_api_without_credentials(monkeypatch):
    def fetch(request, timeout):
        assert request.full_url == "https://api.github.com/repos/junuxyz/cloudrig/releases/latest"
        assert not request.has_header("Authorization")
        assert timeout == 5
        return io.BytesIO(
            json.dumps(
                {
                    "tag_name": "v0.3.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": "example.whl"}],
                }
            ).encode()
        )

    monkeypatch.setattr(updates, "urlopen", fetch)
    assert updates.latest_release() == updates.Release("0.3.0", frozenset({"example.whl"}))


@pytest.mark.parametrize(
    "change", [{"tag_name": "v0.3.0;echo bad"}, {"draft": True}, {"prerelease": True}, {"assets": [None]}]
)
def test_invalid_release_metadata_is_rejected(monkeypatch, change):
    data = {"tag_name": "v0.3.0", "draft": False, "prerelease": False, "assets": []} | change
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
    with pytest.raises(CrigError, match="Could not check"):
        updates.latest_release()


def test_no_published_release_is_not_an_error(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError(updates.API_URL, 404, "Not Found", {}, None)

    monkeypatch.setattr(updates, "urlopen", missing)
    assert updates.latest_release() is None


def test_notice_checks_once_per_ten_days_and_handles_numeric_versions(monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "__version__", "0.2.9")
    now = [20 * 24 * 60 * 60]
    monkeypatch.setattr(updates.time, "time", lambda: now[0])

    def release():
        calls.append(True)
        return updates.Release("0.2.10", frozenset())

    monkeypatch.setattr(updates, "latest_release", release)
    assert updates.check_notice() == "0.2.10"
    assert updates.check_notice() is None
    assert len(calls) == 1
    now[0] += 10 * 24 * 60 * 60 - 1
    assert updates.check_notice() is None
    assert len(calls) == 1
    now[0] += 1
    assert updates.check_notice() == "0.2.10"
    assert len(calls) == 2


def test_offline_notice_is_silent_and_does_not_retry_each_launch(monkeypatch):
    calls = []

    def offline(*args, **kwargs):
        calls.append(True)
        raise URLError("Offline")

    monkeypatch.setattr(updates, "urlopen", offline)
    assert updates.check_notice() is None
    assert updates.check_notice() is None
    assert calls == [True]
    assert (state.config_dir() / "updates.json").is_file()


@pytest.mark.parametrize("version", ["0.2.4", "0.2.3"])
def test_current_or_older_releases_do_not_notify(monkeypatch, version):
    monkeypatch.setattr(updates, "__version__", "0.2.4")
    monkeypatch.setattr(updates, "latest_release", lambda: updates.Release(version, frozenset()))
    assert updates.check_notice() is None
