import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from cloudrig import state


def test_parallel_process_updates_preserve_every_committed_preference(isolated_home):
    state.update(provider="vastai")
    script = "import sys\nfrom cloudrig import state\nstate.update(**{sys.argv[1]: sys.argv[2]})\n"

    def update(index):
        return subprocess.run(
            [sys.executable, "-c", script, f"process_{index}", f"committed_{index}"],
            capture_output=True,
            text=True,
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(update, range(8)))
    for result in results:
        assert result.returncode == 0, result.stderr
    assert state.load() == {
        "provider": "vastai",
        **{f"process_{index}": f"committed_{index}" for index in range(8)},
    }
    assert (state.config_dir() / "config.json").stat().st_mode & 0o777 == 0o600


def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(monkeypatch):
    state.update(provider="vastai")
    path = state.config_dir() / "config.json"
    original = path.read_bytes()
    real_replace = os.replace

    def fail_config_replace(source, destination):
        if destination == path:
            raise OSError("injected atomic rename failure")
        return real_replace(source, destination)

    with monkeypatch.context() as context:
        context.setattr(os, "replace", fail_config_replace)
        with pytest.raises(OSError, match="injected atomic rename failure"):
            state.update(provider="runpod")
    assert path.read_bytes() == original
    assert state.load() == {"provider": "vastai"}
    assert list(state.config_dir().glob(".config.json.*")) == []
    state.update(provider="runpod")
    assert json.loads(path.read_text()) == {"provider": "runpod"}


def test_symlink_configuration_cannot_overwrite_another_file(tmp_path):
    victim = tmp_path / "unrelated.json"
    victim.write_text('{"untouched": true}')
    root = state.config_dir()
    root.mkdir(parents=True, mode=0o700)
    (root / "config.json").symlink_to(victim)
    with pytest.raises(state.CrigError):
        state.update(provider="vastai")
    assert json.loads(victim.read_text()) == {"untouched": True}
    (root / "config.json").unlink()
    state.update(provider="vastai")
    assert state.load() == {"provider": "vastai"}
