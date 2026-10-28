from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in ("VAST_API_KEY", "VASTAI_API_KEY", "RUNPOD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture(autouse=True)
def no_update_network(monkeypatch):
    from urllib.error import URLError

    def offline(*args, **kwargs):
        raise URLError("Offline test")

    monkeypatch.setattr("cloudrig.updates.urlopen", offline)
