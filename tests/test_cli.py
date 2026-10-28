import json
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cloudrig import broker, state
from cloudrig.cli import action, dispatch, doctor, parser, rent
from cloudrig.connections import default_startup_script
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Rental


def args(*words):
    return parser().parse_intermixed_args(words)


def test_first_run_and_set_do_not_require_credentials(capsys):
    dispatch(args())
    assert "crig setup" in capsys.readouterr().out
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


def test_doctor_checks_versions_and_editor_extension_instead_of_only_paths(monkeypatch, tmp_path, capsys):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    tools = {
        "ssh": "echo OpenSSH_9.9 >&2\n",
        "rsync": "echo 'rsync version 3.4.0'\n",
        "code": "echo ms-vscode-remote.remote-ssh\n",
    }
    for name, body in tools.items():
        (binaries / name).write_text("#!/bin/sh\n" + body)
        (binaries / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(broker, "trusted_root_path", lambda _: None)
    monkeypatch.setattr(broker, "rpc", lambda *a: {"protocol": 3})
    with pytest.raises(CrigError, match="Some checks failed"):
        doctor()
    assert "3.5.0" in capsys.readouterr().out
    (binaries / "rsync").write_text("#!/bin/sh\necho 'rsync version 3.5.1'\n")
    doctor()
    assert "3.5.1" in capsys.readouterr().out


@pytest.mark.parametrize("failed", [False, True])
def test_dashboard_returns_after_action_without_automatically_repeating_it(monkeypatch, failed):
    from cloudrig.cli import dashboard
    from cloudrig.models import InstanceAction

    item = Demo().instances()[0]
    selected = InstanceAction(item, "code")
    results = iter([selected, None])
    views = []
    actions = []

    def application(api, **options):
        views.append(options)
        return SimpleNamespace(run=lambda: next(results))

    def act(api, item, name, paths, options, startup_script):
        actions.append(InstanceAction(item, name))
        if failed:
            raise CrigError("Setup needs attention")

    monkeypatch.setattr("cloudrig.cli.CloudrigApp", application)
    monkeypatch.setattr("cloudrig.cli.action", act)
    dashboard(Demo(), args("--demo", "--rented"))
    assert actions == [selected]
    assert len(views) == 2 and views[1]["selected_id"] == item.id
    assert views[1]["action_error"] == ("Setup needs attention" if failed else None)


@pytest.mark.parametrize("explicit_path,override", [(False, None), (True, None), (False, "echo override")])
def test_dashboard_editor_preserves_saved_project_setup(tmp_path, monkeypatch, explicit_path, override):
    from dataclasses import asdict

    from cloudrig import preparation
    from cloudrig.project import Binding, Phase, Project, save_receipt

    item = replace(Demo().instances()[0], approved=True)
    project = Project(tmp_path, "training", image=Demo.default_image)
    previous = preparation.Preparation(
        "vastai",
        item.id,
        "training",
        project=project,
        setup=True,
        startup_script="echo project startup",
        code_path=project.remote,
    )
    state.update(provider="vastai")
    save_receipt(project, Binding(Phase.UPLOADED, id=item.id))
    folder = preparation.directory("vastai", item.id)
    preparation.private_directory(folder.parent)
    payload = asdict(previous)
    payload["project"]["root"] = str(project.root)
    state.atomic_write(folder / "job.json", json.dumps(payload))
    queued = []
    api = SimpleNamespace(name="vastai", instance=lambda _: item)
    monkeypatch.setattr("cloudrig.cli.start", queued.append)
    action(api, item, "code", ["/workspace/other"] if explicit_path else [], args(), override)
    assert len(queued) == 1
    if explicit_path:
        assert queued[0].code_path == "/workspace/other" and queued[0].project is None
    else:
        assert queued[0] == replace(previous, startup_script=override or previous.startup_script)


def test_dashboard_editor_does_not_silently_repeat_an_incomplete_upload(tmp_path, monkeypatch):
    from dataclasses import asdict

    from cloudrig import preparation
    from cloudrig.project import Binding, Phase, Project, save_receipt

    item = replace(Demo().instances()[0], approved=True)
    project = Project(tmp_path, "training", image=Demo.default_image)
    job = preparation.Preparation("vastai", item.id, "training", project=project, code_path=project.remote)
    save_receipt(project, Binding(Phase.PREPARING, id=item.id))
    folder = preparation.directory("vastai", item.id)
    preparation.private_directory(folder.parent)
    payload = asdict(job)
    payload["project"]["root"] = str(project.root)
    state.atomic_write(folder / "job.json", json.dumps(payload))

    def decline(message):
        assert "overwrite" in message
        raise CrigError("Cancelled")

    monkeypatch.setattr("cloudrig.cli.prompt_confirm", decline)
    monkeypatch.setattr("cloudrig.cli.start", lambda _: pytest.fail("Restarted upload without confirmation"))
    with pytest.raises(CrigError, match="Cancelled"):
        action(SimpleNamespace(name="vastai", instance=lambda _: item), item, "code", [], args())


@pytest.mark.parametrize("query", [None, "5090"])
def test_gpu_flag_opens_market_with_optional_search(monkeypatch, query):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: pytest.fail("Demo contacted service"))
    views = []

    def application(api, **options):
        views.append(options)
        return SimpleNamespace(run=lambda: None)

    monkeypatch.setattr("cloudrig.cli.CloudrigApp", application)
    command = ["--demo", "--gpu"]
    if query:
        command.append(query)
    dispatch(args(*command))
    assert len(views) == 1
    assert views[0]["query"] == (query or "") and views[0]["rented"] is False
    assert not state.config_dir().exists()


def test_gpu_flag_without_query_prints_all_offers_for_scripts(capsys):
    dispatch(args("--demo", "--gpu", "--json"))
    offers = json.loads(capsys.readouterr().out)
    assert len(offers) == len(Demo().offers(""))
    assert len({offer["gpu"] for offer in offers}) > 1
