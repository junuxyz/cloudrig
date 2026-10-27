import json
import subprocess
import sys

import pytest

from cloudrig import state
from cloudrig.cli import dispatch, login, parser
from cloudrig.models import CrigError


def args(*words):
    return parser().parse_intermixed_args(words)


def test_first_run_and_set_do_not_require_credentials(capsys):
    dispatch(args())
    assert "crig --set vastai" in capsys.readouterr().out
    dispatch(args("--set", "vastai"))
    assert state.load()["provider"] == "vastai"
    assert "key" not in json.dumps(state.load())


def test_demo_never_contacts_privileged_service(monkeypatch, capsys):
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: pytest.fail("Demo contacted service"))
    dispatch(args("--demo", "--gpu", "5090", "--json"))
    assert len(json.loads(capsys.readouterr().out)) == 5
    dispatch(args("--demo", "--rented", "--plain"))
    assert "training" in capsys.readouterr().out
    assert not state.config_dir().exists()


def test_transfer_flags_and_named_editor(monkeypatch):
    calls = []
    monkeypatch.setattr("cloudrig.cli.action", lambda *values: calls.append(values))
    dispatch(args("--demo", "training", "--send-to-cloud", "./", "/workspace/"))
    assert calls[-1][2:4] == ("push", ["./", "/workspace/"])
    dispatch(args("--demo", "training", "--send-to-local", "/workspace/", "./"))
    assert calls[-1][2:4] == ("pull", ["/workspace/", "./"])
    dispatch(args("--demo", "training", "code", "."))
    assert calls[-1][2:4] == ("code", ["."])


def test_login_only_invokes_protected_helper(monkeypatch):
    calls = []
    monkeypatch.setattr("cloudrig.broker.admin", lambda *values: calls.append(values))
    monkeypatch.setenv("VAST_API_KEY", "must-not-be-read")
    login("vastai")
    assert calls == [("login", "vastai")]
    assert not state.config_dir().exists()


def test_private_key_override_is_disabled(monkeypatch):
    from pathlib import Path

    with pytest.raises(CrigError, match="Private-key paths"):
        login("vastai", Path("/tmp/secret"))
    state.update(provider="vastai")
    with pytest.raises(CrigError, match="disabled"):
        dispatch(args("--identity", "/tmp/secret", "--gpu", "5090"))


def test_entrypoint_json_and_remote_command_flags():
    result = subprocess.run(
        [sys.executable, "-m", "cloudrig.cli", "--demo", "--gpu", "5090", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(json.loads(result.stdout)) == 5
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cloudrig.cli",
            "--demo",
            "training",
            "exec",
            "--",
            "python",
            "-c",
            "print(1)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Demo: exec" in result.stdout


def test_state_contains_preferences_only_and_is_private():
    state.update(provider="runpod")
    path = state.config_dir() / "config.json"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("invalid json")
    with pytest.raises(CrigError):
        state.load()
