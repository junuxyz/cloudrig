import subprocess

import pytest

from cloudrig import broker
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Rental


def test_privileged_calls_always_use_fresh_sudo_and_fixed_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(broker, "trusted_root_path", lambda _: None)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true,"id":"created-1"}')

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("VAST_API_KEY", "secret")
    for operation in ["login", "approve", "start", "destroy"]:
        broker.admin(operation, "vastai", "1")
    assert len(calls) == 4
    for argv, kwargs in calls:
        assert argv[:4] == ["/usr/bin/sudo", "-k", "--", str(broker.HELPER)]
        assert "VAST_API_KEY" not in kwargs["env"]
        assert "SSH_AUTH_SOCK" not in kwargs["env"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs.get("shell") is not True


def test_mutations_do_not_go_through_read_service(monkeypatch):
    privileged, normal = [], []
    monkeypatch.setattr(broker, "admin", lambda *v: privileged.append(v) or {"id": "created"})
    monkeypatch.setattr(broker, "rpc", lambda *a, **k: normal.append((a, k)))
    api = broker.BrokerProvider("vastai")
    offer = Demo().offers("5090")[0]
    assert api.rent(Rental(offer, "training", 40, api.default_image)) == "created"
    api.start("1")
    api.destroy("1")
    api.approve("1")
    api.stop("1")
    assert [row[0] for row in privileged] == ["rent", "start", "destroy", "approve"]
    assert normal == [(("stop", "vastai"), {"id": "1"})]


def test_user_owned_binary_path_is_not_a_trusted_helper(tmp_path):
    binary = tmp_path / "fake-helper"
    binary.write_text("not a privileged helper")
    with pytest.raises(CrigError):
        broker.trusted_root_path(binary)


def test_helper_never_accepts_arbitrary_command(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Command executed"))
    with pytest.raises(CrigError):
        broker.admin("exec", "/bin/sh")


def test_missing_service_does_not_fall_back_to_environment_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(broker, "PUBLIC_ROOT", tmp_path / "missing")
    monkeypatch.setenv("RUNPOD_API_KEY", "a-real-looking-but-unused-key")
    with pytest.raises(CrigError, match="Protected cloudrig service"):
        broker.BrokerProvider("runpod").instances()
