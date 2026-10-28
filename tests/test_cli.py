import json
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cloudrig import state
from cloudrig.cli import action, dispatch, parser
from cloudrig.connections import default_startup_script
from cloudrig.demo import Demo
from cloudrig.models import CrigError


def args(*words):
    return parser().parse_intermixed_args(words)


def test_first_run_and_set_do_not_require_credentials(capsys):
    dispatch(args())
    assert "crig --set vastai" in capsys.readouterr().out
    dispatch(args("--set", "vastai"))
    assert state.load()["provider"] == "vastai"


def test_default_startup_can_be_exported_before_account_setup(capsys):
    dispatch(args("startup"))
    assert capsys.readouterr().out == default_startup_script()
    assert not state.config_dir().exists()


def test_missing_startup_fails_before_rental_or_provider_access(monkeypatch):
    state.update(provider="vastai")
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: pytest.fail("Contacted provider"))
    with pytest.raises(CrigError, match="Cannot read startup script"):
        dispatch(args("--gpu", "5090", "--rent", "84021", "--startup", "missing.sh"))


def test_demo_never_contacts_privileged_service(monkeypatch, capsys):
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: pytest.fail("Demo contacted service"))
    dispatch(args("--demo", "--gpu", "5090", "--json"))
    assert len(json.loads(capsys.readouterr().out)) == 5
    dispatch(args("--demo", "--rented", "--plain"))
    assert "training" in capsys.readouterr().out
    assert not state.config_dir().exists()


def test_entrypoint_preserves_remote_command_flags():
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


def test_corrupt_configuration_reports_an_error():
    state.update(provider="runpod")
    path = state.config_dir() / "config.json"
    path.write_text("invalid json")
    with pytest.raises(CrigError):
        state.load()


@pytest.mark.parametrize("connection", ["external", "preparing", "ready"])
def test_connect_handles_authorization_and_pending_setup_inline(connection, monkeypatch):
    calls = []
    item = replace(
        Demo().instances()[0],
        approved=connection != "external",
        agent_socket="/protected/agent.sock" if connection == "ready" else None,
    )
    api = SimpleNamespace(
        name="vastai",
        instance=lambda _: item,
        approve=lambda identifier: calls.append(("approve", identifier)),
    )
    monkeypatch.setattr("cloudrig.cli.wait_ready", lambda *a, **k: calls.append("wait") or item)
    monkeypatch.setattr("cloudrig.cli.open_code", lambda *a: calls.append("code"))
    action(api, item, "code", [], args("--wait"))
    expected = [("approve", item.id)] if connection == "external" else []
    assert calls == [*expected, "wait", "code"]


def test_rental_returns_after_recording_id_and_queuing_setup(monkeypatch, tmp_path):
    from cloudrig.cli import rent
    from cloudrig.models import Rental

    jobs = []
    api = SimpleNamespace(name="vastai", rent=lambda _: "42")
    monkeypatch.setattr("cloudrig.cli.start", lambda job: jobs.append(job) or tmp_path)
    monkeypatch.setattr("cloudrig.workspace.preflight", lambda **kwargs: None)
    monkeypatch.setattr("cloudrig.cli.wait_ready", lambda *a, **k: pytest.fail("Waited in caller"))
    rental = Rental(Demo().offers("5090")[0], "training", 40, Demo.default_image)
    rent(api, rental, args(), "echo startup")
    assert state.load()["active"]["vastai"] == "42"
    assert jobs[0].identifier == "42" and jobs[0].code_path == "/workspace"
    assert jobs[0].startup_script == "echo startup"
