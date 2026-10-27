import json
import subprocess
import sys

import pytest

from cloudrig import state
from cloudrig.cli import dispatch, parser
from cloudrig.models import CrigError


def args(*words):
    return parser().parse_intermixed_args(words)


def test_first_run_and_set_do_not_require_credentials(capsys):
    dispatch(args())
    assert "crig --set vastai" in capsys.readouterr().out
    dispatch(args("--set", "vastai"))
    assert state.load()["provider"] == "vastai"


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
