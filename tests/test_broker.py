import os
import subprocess
import sys

import pytest

from cloudrig import broker
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Rental, RentalNotSubmitted


def test_privileged_calls_always_use_fresh_sudo_and_fixed_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(broker, "trusted_root_path", lambda _: None)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true,"id":"created-1"}')

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("VAST_API_KEY", "secret")
    for operation in ["login", "approve", "stop", "destroy"]:
        broker.admin(operation, "vastai", "1")
    assert len(calls) == 4
    for argv, kwargs in calls:
        assert argv[:4] == ["/usr/bin/sudo", "-k", "--", str(broker.HELPER)]
        assert "VAST_API_KEY" not in kwargs["env"]
        assert "SSH_AUTH_SOCK" not in kwargs["env"]
        assert kwargs["stdin"] is None
        assert kwargs.get("shell") is not True


def test_mutations_do_not_go_through_read_service(monkeypatch):
    privileged, normal = [], []
    monkeypatch.setattr(broker, "admin", lambda *v: privileged.append(v) or {"id": "created"})
    monkeypatch.setattr(broker, "rpc", lambda *a, **k: normal.append((a, k)) or {"protocol": 4})
    api = broker.BrokerProvider("vastai")
    offer = Demo().offers("5090")[0]
    assert api.rent(Rental(offer, "training", 40, api.default_image)) == "created"
    api.destroy("1")
    api.approve("1")
    api.stop("1")
    assert [row[0] for row in privileged] == ["rent", "destroy", "approve", "stop"]
    assert normal == [(("health", "vastai"), {})]


def test_user_owned_binary_path_is_not_a_trusted_helper(tmp_path):
    binary = tmp_path / "fake-helper"
    binary.write_text("not a privileged helper")
    with pytest.raises(CrigError):
        broker.trusted_root_path(binary)


def test_writable_runtime_parent_reports_the_failing_path(tmp_path, monkeypatch):
    runtime = tmp_path / "system-run"
    public = runtime / "cloudrig"
    public.mkdir(parents=True)
    runtime.chmod(0o775)
    monkeypatch.setattr(broker, "ROOT_UID", os.getuid())
    with pytest.raises(CrigError) as error:
        broker.trusted_root_path(public)
    assert str(runtime) in str(error.value)
    assert "mode=0775" in str(error.value)


@pytest.mark.skipif(
    sys.platform != "darwin" or not broker.HELPER.parent.is_dir(),
    reason="requires the macOS privileged-helper directory",
)
def test_macos_runtime_parent_passes_real_ownership_check():
    broker.trusted_root_path(broker.PUBLIC_ROOT.parent)


def test_helper_never_accepts_arbitrary_command(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Command executed"))
    with pytest.raises(CrigError):
        broker.admin("exec", "/bin/sh")


def test_missing_service_does_not_fall_back_to_environment_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(broker, "PUBLIC_ROOT", tmp_path / "missing")
    monkeypatch.setenv("RUNPOD_API_KEY", "a-real-looking-but-unused-key")
    with pytest.raises(CrigError, match="Cloudrig service is unavailable"):
        broker.BrokerProvider("runpod").instances()


@pytest.mark.parametrize("recorded", ["absent", "pending", "allocated", "unavailable"])
def test_failed_rental_is_retryable_only_when_the_protected_journal_confirms_no_submission(
    recorded, monkeypatch
):
    def fail(*args, **kwargs):
        raise CrigError("operation failed")

    monkeypatch.setattr(broker, "admin", fail)

    def rpc(operation, *args, **kwargs):
        if operation == "health":
            return {"protocol": 4}
        if recorded == "unavailable":
            fail()
        return {"state": recorded}

    monkeypatch.setattr(broker, "rpc", rpc)
    api = broker.BrokerProvider("vastai")
    with pytest.raises(CrigError) as error:
        api.rent(Rental(Demo().offers("5090")[0], "training", 40, api.default_image))
    assert isinstance(error.value, RentalNotSubmitted) == (recorded == "absent")
    assert "operation failed" in str(error.value)


@pytest.mark.parametrize(
    "source, reason",
    [
        ("cloudrig-admin: offer disappeared; refresh and retry", "offer disappeared"),
        ("cloudrig-admin: cancelled; nothing was submitted", "cancelled"),
        ("sudo: a password is required", "a password is required"),
    ],
)
def test_rental_failure_keeps_helper_reason_instead_of_hiding_it(monkeypatch, source, reason):
    monkeypatch.setattr(broker, "trusted_root_path", lambda _: None)

    def run(argv, **kwargs):
        assert kwargs["stderr"] == subprocess.PIPE
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="RENT training\n" + source + "\n")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        broker,
        "rpc",
        lambda operation, *a, **k: {"protocol": 4} if operation == "health" else {"state": "absent"},
    )
    api = broker.BrokerProvider("vastai")
    with pytest.raises(RentalNotSubmitted) as error:
        api.rent(Rental(Demo().offers("5090")[0], "training", 40, api.default_image))
    assert reason in str(error.value)
    assert "No rental was submitted" in str(error.value)
    assert "RENT training" not in str(error.value)


def test_old_helper_is_rejected_before_authentication_or_rental(monkeypatch):
    monkeypatch.setattr(broker, "rpc", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(broker, "admin", lambda *a: pytest.fail("Invoked an incompatible helper"))
    api = broker.BrokerProvider("vastai")
    with pytest.raises(RentalNotSubmitted, match="service update"):
        api.rent(Rental(Demo().offers("5090")[0], "training", 40, api.default_image))
