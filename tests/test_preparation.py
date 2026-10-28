import json
import os
import subprocess
import time
from contextlib import nullcontext
from dataclasses import asdict
from textwrap import dedent

import pytest

from cloudrig import preparation, state
from cloudrig.models import CrigError
from cloudrig.preparation import Preparation, read_status
from cloudrig.project import Binding, Phase, Project, project_lock, save_receipt


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    pytest.fail("Background setup did not reach the expected state")


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("project_job", [False, True])
def test_detached_setup_reuses_one_worker_and_reports_completion(tmp_path, monkeypatch, failed, project_job):
    release = tmp_path / "release"
    opened = tmp_path / "opened"
    notification = tmp_path / "notification"
    session = tmp_path / "session"
    connected = tmp_path / "connected"
    driver = tmp_path / "worker.py"
    driver.write_text(
        dedent(f"""\
        import os
        import sys
        import time
        from pathlib import Path

        from cloudrig import preparation, workspace
        from cloudrig.demo import Demo
        from cloudrig.models import CrigError

        class ReadOnlyProvider:
            def __init__(self, name):
                Path({str(connected)!r}).touch()

            def rent(self, *args):
                raise AssertionError("Worker attempted a rental")

            def start(self, *args):
                raise AssertionError("Worker attempted a paid start")

            def instance(self, identifier):
                return Demo().instances()[0]

        def ready(api, identifier, **options):
            Path({str(session)!r}).write_text(str(os.getsid(0) == os.getpid()))
            options["progress"]("Running startup script")
            while not Path({str(release)!r}).exists():
                time.sleep(0.02)
            if {failed!r}:
                raise CrigError("Startup failed")
            return Demo().instances()[0]

        def prepare(project, api, binding, options, **kwargs):
            assert binding.id == "42" and not options.open_editor
            ready(api, binding.id, **kwargs)

        preparation.BrokerProvider = ReadOnlyProvider
        preparation.wait_ready = ready
        workspace.prepare = prepare
        preparation.open_code = lambda item, path: Path({str(opened)!r}).write_text(path)
        preparation.notify = lambda title, message: Path({str(notification)!r}).write_text(title)
        sys.exit(preparation.run(Path(sys.argv[1])))
        """)
    )
    actual_popen = subprocess.Popen
    processes = []

    def launch(argv, **options):
        assert options["stdin"] == subprocess.DEVNULL
        assert options["start_new_session"]
        assert "VAST_API_KEY" not in options["env"]
        process = actual_popen([argv[0], str(driver), argv[-1]], **options)
        processes.append(process)
        return process

    monkeypatch.setenv("VAST_API_KEY", "SECRET")
    monkeypatch.setattr(preparation.subprocess, "Popen", launch)
    project = Project(tmp_path, "training") if project_job else None
    if project:
        save_receipt(project, Binding(Phase.PREPARING, id="42"))
    job = Preparation("vastai", "42", "training", startup_script="echo setup", project=project)
    try:
        with project_lock(project) if project else nullcontext():
            folder = preparation.start(job)
            if project:
                wait_for(connected.exists)
                assert not session.exists()
        wait_for(lambda: read_status(folder).get("step") == "Running startup script")
        assert session.read_text() == "True"
        assert preparation.start(job) == folder
        assert len(processes) == 1
        assert (folder / "job.json").stat().st_mode & 0o077 == 0
        release.touch()
        assert processes[0].wait(timeout=10) == int(failed)
        status = read_status(folder)
        assert status["phase"] == ("failed" if failed else "ready")
        assert [event["step"] for event in status["history"][:2]] == [
            "Waiting for the GPU",
            "Running startup script",
        ]
        assert status["history"][-1]["step"] == ("Startup failed" if failed else "Ready")
        assert [event["at"] for event in status["history"]] == sorted(
            event["at"] for event in status["history"]
        )
        assert notification.read_text() == ("Cloudrig setup failed" if failed else "Cloudrig ready")
        assert opened.exists() != failed
        if not failed:
            assert opened.read_text() == "/workspace"
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def test_status_reads_saved_progress_without_provider_access(monkeypatch, capsys):
    folder = preparation.directory("vastai", "42")
    state.atomic_write(
        folder / "status.json",
        json.dumps(
            {
                "id": "42",
                "provider": "vastai",
                "name": "training",
                "phase": "ready",
                "step": "Ready",
                "started": 100,
                "finished": 165,
            }
        ),
    )
    monkeypatch.setattr(preparation, "BrokerProvider", lambda _: pytest.fail("Status contacted provider"))
    preparation.show_status("training")
    assert "ready · 1m 5s" in capsys.readouterr().out


def test_notification_passes_text_as_data_and_delivery_failure_is_nonfatal(monkeypatch):
    calls = []
    monkeypatch.setattr(preparation.sys, "platform", "darwin")

    def fail(argv, **kwargs):
        calls.append(argv)
        raise OSError("notifications unavailable")

    monkeypatch.setattr(preparation.subprocess, "run", fail)
    message = 'name " & do shell script "unexpected"'
    preparation.notify("Cloudrig ready", message)
    assert calls[0][-1] == message and message not in calls[0][2]


def test_active_setup_rejects_a_different_project_instead_of_losing_the_request(tmp_path):
    job = Preparation("vastai", "42", "training")
    folder = preparation.directory("vastai", "42")
    preparation.private_directory(folder.parent)
    preparation.private_directory(folder)
    state.atomic_write(folder / "job.json", json.dumps(asdict(job)))
    state.atomic_write(folder / "status.json", json.dumps({"phase": "queued", "pid": os.getpid()}))
    assert preparation.start(job) == folder
    project = Project(tmp_path, "other")
    with pytest.raises(CrigError, match="different setup"):
        preparation.start(Preparation("vastai", "42", "training", project=project))
    assert json.loads((folder / "job.json").read_text())["project"] is None


def test_unexpected_worker_error_keeps_safe_diagnostic_locations(monkeypatch):
    job = Preparation("vastai", "42", "training")
    folder = preparation.directory("vastai", "42")
    preparation.private_directory(folder.parent)
    preparation.private_directory(folder)
    state.atomic_write(folder / "job.json", json.dumps(asdict(job)))
    state.atomic_write(folder / "status.json", json.dumps({"phase": "queued"}))

    def fail(_):
        raise RuntimeError("PRIVATE_TOKEN")

    monkeypatch.setattr(preparation, "BrokerProvider", fail)
    monkeypatch.setattr(preparation, "notify", lambda *a: None)
    assert preparation.run(folder) == 1
    status = (folder / "status.json").read_text()
    assert "PRIVATE_TOKEN" not in status
    assert json.loads(status)["diagnostic"]["type"] == "RuntimeError"
    assert json.loads(status)["diagnostic"]["frames"][-1]["function"] == "fail"


def test_reconnect_restarts_abandoned_setup_even_when_its_pid_is_alive(monkeypatch):
    from types import SimpleNamespace

    job = Preparation("vastai", "42", "training")
    folder = preparation.directory("vastai", "42")
    preparation.private_directory(folder.parent)
    preparation.private_directory(folder)
    state.atomic_write(folder / "job.json", json.dumps(asdict(job)))
    state.atomic_write(folder / "status.json", json.dumps({"phase": "preparing", "pid": os.getpid()}))
    children = []
    monkeypatch.setattr(
        preparation.subprocess,
        "Popen",
        lambda *a, **k: children.append(a) or SimpleNamespace(pid=os.getpid()),
    )
    assert preparation.start(job) == folder
    assert len(children) == 1
    assert read_status(folder)["phase"] == "queued"
