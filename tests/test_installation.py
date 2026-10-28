import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cloudrig import __version__, installation, onboarding, updates
from cloudrig.models import CrigError


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    folder = tmp_path / "release files"
    folder.mkdir()
    files = ("cloudrig-admin", "install-admin.sh", "uninstall-admin.sh")
    for name in files:
        (folder / name).write_bytes(b"test payload " + name.encode())
    manifest = {
        "version": __version__,
        "system": "darwin",
        "arch": "arm64",
        "sha256": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(installation, "BUNDLE", folder)
    monkeypatch.setattr(installation.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installation.platform, "machine", lambda: "arm64")
    return folder


@pytest.mark.parametrize("damage", ["binary", "installer", "missing", "platform", "version"])
def test_invalid_release_never_requests_sudo_or_executes(monkeypatch, bundle, damage):
    if damage in {"binary", "installer"}:
        (bundle / ("cloudrig-admin" if damage == "binary" else "install-admin.sh")).write_text("changed")
    elif damage == "missing":
        (bundle / "manifest.json").unlink()
    else:
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest["arch" if damage == "platform" else "version"] = "other"
        (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(installation, "prompt_confirm", lambda _: pytest.fail("Requested approval"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Executed damaged release"))
    with pytest.raises(CrigError, match="missing, damaged"):
        installation.ensure_service("vastai")


def test_matching_running_helper_needs_no_installation(monkeypatch, bundle):
    monkeypatch.setattr(installation, "HELPER", bundle / "cloudrig-admin")
    monkeypatch.setattr(installation, "trusted_root_path", lambda _: None)
    monkeypatch.setattr(installation, "check_service", lambda _: None)
    monkeypatch.setattr(installation, "prompt_confirm", lambda _: pytest.fail("Repeated installation"))
    installation.ensure_service("vastai")


def test_service_update_uses_packaged_installer_without_compiler_or_login(monkeypatch, bundle, tmp_path):
    calls = []
    old = tmp_path / "old-helper"
    old.write_text("old")
    monkeypatch.setattr(installation, "HELPER", old)
    monkeypatch.setattr(installation, "trusted_root_path", lambda _: None)
    monkeypatch.setattr(installation, "check_service", lambda _: calls.append("health"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installation, "prompt_confirm", lambda _: calls.append("confirm"))
    monkeypatch.setattr(installation.shutil, "which", lambda _: pytest.fail("Looked for compiler"))
    monkeypatch.setattr("cloudrig.cli.login", lambda _: pytest.fail("Replaced API key"))
    monkeypatch.setattr(onboarding.Prompt, "ask", lambda *a, **k: pytest.fail("Asked for provider"))

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    assert onboarding.setup(["--service-only"])
    assert calls == [
        "health",
        "confirm",
        [
            "/usr/bin/sudo",
            "-k",
            "--",
            "/bin/sh",
            str(bundle / "install-admin.sh"),
            str(bundle / "cloudrig-admin"),
        ],
        "health",
    ]


def test_cancelled_packaged_installation_never_executes(monkeypatch, bundle):
    monkeypatch.setattr(installation, "HELPER", bundle / "absent")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def decline(_):
        raise CrigError("Cancelled")

    monkeypatch.setattr(installation, "prompt_confirm", decline)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Executed after cancellation"))
    with pytest.raises(CrigError, match="Cancelled"):
        installation.ensure_service("vastai")


@pytest.mark.parametrize("failure", [None, "download", "verify", "install", "service"])
def test_official_update_verifies_before_install_and_service(monkeypatch, bundle, failure):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installation.shutil, "which", lambda name: f"/tools/{name}")
    name = "cloudrig-0.4.0-py3-none-macosx_13_0_arm64.whl"
    monkeypatch.setattr(updates, "latest_release", lambda: updates.Release("0.4.0", frozenset({name})))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        phase = ("download", "verify", "install", "service")[len(calls) - 1]
        return SimpleNamespace(returncode=int(phase == failure))

    monkeypatch.setattr(subprocess, "run", run)
    if failure:
        with pytest.raises(CrigError):
            installation.update([])
    else:
        installation.update([])
    expected = ("download", "verify", "install", "service")
    assert len(calls) == (expected.index(failure) + 1 if failure else 4)
    assert calls[0][:4] == ["/tools/gh", "release", "download", "v0.4.0"]
    assert calls[0][calls[0].index("--repo") + 1] == "junuxyz/cloudrig"
    if len(calls) > 1:
        assert calls[1][:3] == ["/tools/gh", "attestation", "verify"]
        assert calls[1][calls[1].index("--source-ref") + 1] == "refs/tags/v0.4.0"
    if len(calls) > 2:
        assert calls[2][:4] == ["/tools/uv", "tool", "install", "--force"]
    if len(calls) > 3:
        assert calls[3] == ["/tools/crig", "setup", "--service-only"]


def test_missing_platform_release_never_downloads_or_installs(monkeypatch, bundle):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installation.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(updates, "latest_release", lambda: updates.Release("0.4.0", frozenset()))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Executed incomplete update"))
    with pytest.raises(CrigError, match="no wheel for this platform"):
        installation.update([])


@pytest.mark.parametrize("removal_code", [0, 1])
def test_uninstall_removes_command_only_after_service_success(monkeypatch, bundle, removal_code):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installation.shutil, "which", lambda _: "/tools/uv")
    monkeypatch.setattr(installation, "prompt_confirm", lambda _: None)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=removal_code if len(calls) == 1 else 0)

    monkeypatch.setattr(subprocess, "run", run)
    if removal_code:
        with pytest.raises(CrigError, match="Service removal failed"):
            installation.uninstall([])
    else:
        installation.uninstall([])
    assert calls[0] == ["/usr/bin/sudo", "-k", "--", "/bin/sh", str(bundle / "uninstall-admin.sh")]
    assert calls[1:] == ([] if removal_code else [["/tools/uv", "tool", "uninstall", "cloudrig"]])
