import json
import subprocess
import time
from contextlib import nullcontext

import pytest

from cloudrig import preparation, state
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
def test_detached_setup_reuses_one_worker_and_reports_completion(
    tmp_path, monkeypatch, failed, project_job
):
    release = tmp_path / "release"
    opened = tmp_path / "opened"
    notification = tmp_path / "notification"
    session = tmp_path / "session"
    connected = tmp_path / "connected"
    driver = tmp_path / "worker.py"
    driver.write_text(
        "import os,sys,time\nfrom pathlib import Path\n"
        "from cloudrig import preparation\nfrom cloudrig.demo import Demo\n"
        "from cloudrig.models import CrigError\n"
        "class ReadOnlyProvider:\n"
        f" def __init__(self,name): Path({str(connected)!r}).touch()\n"
        " def rent(self,*args): raise AssertionError('Worker attempted a rental')\n"
        " def start(self,*args): raise AssertionError('Worker attempted a paid start')\n"
        " def instance(self,identifier): return Demo().instances()[0]\n"
        "def ready(api,identifier,**options):\n"
        f" Path({str(session)!r}).write_text(str(os.getsid(0)==os.getpid()))\n"
        " options['progress']('Running startup script')\n"
        f" while not Path({str(release)!r}).exists(): time.sleep(.02)\n"
        f" if {failed!r}: raise CrigError('Startup failed')\n"
        " return Demo().instances()[0]\n"
        "preparation.BrokerProvider=ReadOnlyProvider\npreparation.wait_ready=ready\n"
        "from cloudrig import workspace\n"
        "def prepare(project,api,binding,options,**kwargs):\n"
        " assert binding.id=='42' and not options.open_editor\n"
        " ready(api,binding.id,**kwargs)\n"
        "workspace.prepare=prepare\n"
        f"preparation.open_code=lambda item,path: Path({str(opened)!r}).write_text(path)\n"
        f"preparation.notify=lambda title,message: Path({str(notification)!r}).write_text(title)\n"
        "sys.exit(preparation.run(Path(sys.argv[1])))\n"
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
