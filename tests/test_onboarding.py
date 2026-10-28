import subprocess
import sys
from types import SimpleNamespace

import pytest

from cloudrig import installation, onboarding, state, workspace_cli
from cloudrig.cli import main
from cloudrig.demo import Demo
from cloudrig.models import CrigError
from cloudrig.project import load_project


def test_setup_without_terminal_fails_before_configuration_changes():
    with pytest.raises(CrigError, match="own terminal"):
        onboarding.setup([])
    assert not state.config_dir().exists()


def test_cancel_setup_does_not_check_tools_or_service(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.Prompt, "ask", lambda *a, **k: "0")
    monkeypatch.setattr(onboarding, "ensure_service", lambda *a: pytest.fail("Contacted service"))
    assert onboarding.setup([]) is False
    assert not state.config_dir().exists()


@pytest.mark.parametrize("login_fails", [False, True])
def test_existing_service_setup_saves_provider_only_after_successful_login(monkeypatch, login_fails):
    calls = []
    state.update(provider="runpod")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.Prompt, "ask", lambda *a, **k: "1")
    monkeypatch.setattr("cloudrig.workspace.preflight", lambda **k: None)
    monkeypatch.setattr(onboarding, "ensure_service", lambda p, source: calls.append(("health", p)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Installed healthy service"))

    def login(provider):
        calls.append(("login", provider))
        if login_fails:
            raise CrigError("Login cancelled")

    monkeypatch.setattr("cloudrig.cli.login", login)
    if login_fails:
        with pytest.raises(CrigError, match="Login cancelled"):
            onboarding.setup([])
    else:
        assert onboarding.setup([]) is True
    assert state.load()["provider"] == ("runpod" if login_fails else "vastai")
    assert calls == [("health", "vastai"), ("login", "vastai")]


@pytest.mark.parametrize("build_fails", [False, True])
def test_source_installation_keeps_build_sudo_health_login_in_order(monkeypatch, tmp_path, build_fails):
    calls = []
    source = tmp_path / "Cloudrig source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts/install-admin.sh").touch()
    (source / "admin").mkdir()
    (source / "admin/go.mod").touch()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.Prompt, "ask", lambda *a, **k: "1")
    monkeypatch.setattr("cloudrig.workspace.preflight", lambda **k: None)
    monkeypatch.setattr(installation.shutil, "which", lambda _: "/usr/local/bin/go")
    monkeypatch.setattr(installation, "check_service", lambda _: calls.append("health"))

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if build_fails else 0)

    monkeypatch.setattr(installation, "prompt_confirm", lambda _: calls.append("confirm"))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("cloudrig.cli.login", lambda _: calls.append("login"))
    build = ["/bin/sh", str(source / "scripts/build-admin.sh")]
    if build_fails:
        with pytest.raises(CrigError, match="Helper build failed"):
            onboarding.setup(["--source", str(source)])
        assert calls == ["confirm", build]
        assert not state.config_dir().exists()
    else:
        onboarding.setup(["--source", str(source)])
        assert calls == [
            "confirm",
            build,
            [
                "/usr/bin/sudo",
                "-k",
                "--",
                "/bin/sh",
                str(source / "scripts/install-admin.sh"),
                str(source / "dist/cloudrig-admin"),
            ],
            "health",
            "login",
        ]
        assert state.load()["provider"] == "vastai"


def test_numbered_offer_review_paginates_and_requires_confirmation(monkeypatch, tmp_path):
    project = load_project(str(tmp_path))
    rows = Demo().offers("5090")
    rows = rows + rows[:2]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["6", "2"])
    monkeypatch.setattr(workspace_cli.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(workspace_cli.WorkspaceApp, "run", lambda _: pytest.fail("Opened full-screen UI"))
    reviewed = []
    monkeypatch.setattr(workspace_cli, "prompt_confirm", reviewed.append)
    selected = workspace_cli.choose_offer(project, rows, setup=True, numbered=True)
    assert selected == rows[6]
    assert len(reviewed) == 1 and "Billing starts" in reviewed[0]


def test_cancelled_offer_review_returns_without_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(workspace_cli.Prompt, "ask", lambda *a, **k: "0")
    monkeypatch.setattr(workspace_cli, "prompt_confirm", lambda _: pytest.fail("Confirmed rental"))
    assert (
        workspace_cli.choose_offer(
            load_project(str(tmp_path)), Demo().offers("5090"), setup=False, numbered=True
        )
        is None
    )
    assert not state.config_dir().exists()


def test_declining_installation_never_builds_installs_or_logs_in(monkeypatch, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/install-admin.sh").touch()
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin/go.mod").touch()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding.Prompt, "ask", lambda *a, **k: "1")
    monkeypatch.setattr("cloudrig.workspace.preflight", lambda **k: None)
    monkeypatch.setattr(installation.shutil, "which", lambda _: "/usr/local/bin/go")

    def unavailable(_):
        raise CrigError("Service unavailable")

    def decline(_):
        raise CrigError("Cancelled")

    monkeypatch.setattr(installation, "check_service", unavailable)
    monkeypatch.setattr(installation, "prompt_confirm", decline)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Executed after cancellation"))
    monkeypatch.setattr("cloudrig.cli.login", lambda _: pytest.fail("Requested credentials"))
    with pytest.raises(CrigError, match="Cancelled"):
        onboarding.setup(["--source", str(tmp_path)])
    assert not state.config_dir().exists()


def test_declining_numbered_rental_does_not_submit_or_save_binding(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(workspace_cli.Prompt, "ask", lambda *a, **k: "1")
    api = Demo()
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Submitted rental"), raising=False)

    def decline(_):
        raise CrigError("Cancelled")

    monkeypatch.setattr(workspace_cli, "prompt_confirm", decline)
    with pytest.raises(CrigError, match="Cancelled"):
        workspace_cli.dispatch(["up", str(tmp_path), "--no-code", "--numbered"])
    assert not state.load().get("projects")


def test_default_command_opens_dashboard_for_existing_account(monkeypatch):
    state.update(provider="vastai")
    monkeypatch.setattr(sys, "argv", ["crig"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(onboarding, "setup", lambda _: pytest.fail("Repeated onboarding"))
    monkeypatch.setattr(installation, "ensure_service", lambda _: None)
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: Demo())
    calls = []

    def dashboard(api, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(run=lambda: calls.append("dashboard opened"))

    monkeypatch.setattr("cloudrig.cli.CloudrigApp", dashboard)
    main()
    assert calls[0]["rented"] is True
    assert calls[1] == "dashboard opened"


def test_first_setup_continues_into_dashboard(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crig"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def setup(_):
        state.update(provider="vastai")
        return True

    monkeypatch.setattr(onboarding, "setup", setup)
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: Demo())
    opened = []
    monkeypatch.setattr(
        "cloudrig.cli.CloudrigApp", lambda *a, **k: SimpleNamespace(run=lambda: opened.append(True))
    )
    main()
    assert opened == [True]


def test_project_review_uses_full_screen_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    project = load_project(str(tmp_path))
    rows = Demo().offers("5090")
    monkeypatch.setattr(workspace_cli.Prompt, "ask", lambda *a, **k: pytest.fail("Numbered prompt"))
    monkeypatch.setattr(workspace_cli.WorkspaceApp, "run", lambda _: rows[0])
    assert workspace_cli.choose_offer(project, rows, setup=False, numbered=False) == rows[0]
