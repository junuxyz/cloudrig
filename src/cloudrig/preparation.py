"""Prepare an allocated GPU in a separate ordinary-user process."""

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from cloudrig import state
from cloudrig.broker import BrokerProvider
from cloudrig.connections import open_code, wait_ready
from cloudrig.models import CrigError, instance_id, provider_name
from cloudrig.project import Project, project_lock, receipt
from cloudrig.security import child_environment, private_directory, regular_file
from cloudrig.terminal import console


@dataclass(frozen=True)
class Preparation:
    provider: str
    identifier: str
    name: str
    timeout: int = 300
    startup_script: str | None = None
    code_path: str | None = "/workspace"
    project: Project | None = None
    setup: bool = False


def directory(provider: str, identifier: str) -> Path:
    provider_name(provider)
    instance_id(identifier)
    return state.config_dir() / "preparations" / f"{provider}-{identifier}"


def read_status(folder: Path) -> dict:
    path = folder / "status.json"
    regular_file(path, private=True)
    if not path.exists():
        return {}
    record = json.loads(path.read_text())
    if record["phase"] in {"queued", "preparing"} and record.get("pid"):
        try:
            os.kill(record["pid"], 0)
        except ProcessLookupError:
            record.update(phase="interrupted", step="Setup stopped; reconnect to retry")
    return record


def start(job: Preparation) -> Path:
    folder = directory(job.provider, job.identifier)
    private_directory(folder.parent)
    private_directory(folder)
    lock = folder / "worker.lock"
    regular_file(lock, private=True)
    try:
        with FileLock(lock, timeout=0, mode=0o600):
            previous = read_status(folder)
            if previous.get("phase") in {"queued", "preparing"}:
                return folder
            payload = asdict(job)
            if job.project:
                payload["project"]["root"] = str(job.project.root)
            state.atomic_write(folder / "job.json", json.dumps(payload))
            state.atomic_write(folder / "setup.log", "")
            environment = child_environment()
            if "XDG_CONFIG_HOME" in os.environ:
                environment["XDG_CONFIG_HOME"] = os.environ["XDG_CONFIG_HOME"]
            with (folder / "setup.log").open("a") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "cloudrig.preparation", str(folder)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=environment,
                )
            state.atomic_write(
                folder / "status.json",
                json.dumps(
                    {
                        "provider": job.provider,
                        "id": job.identifier,
                        "name": job.name,
                        "phase": "queued",
                        "step": "Starting setup",
                        "pid": process.pid,
                        "started": time.time(),
                    }
                ),
            )
    except Timeout:
        pass  # The existing worker already owns this instance's setup.
    return folder


def notify(title: str, message: str) -> None:
    if sys.platform == "darwin":
        script = (
            "on run argv\n"
            'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\n'
            "end run"
        )
        command = ["/usr/bin/osascript", "-e", script, title, message]
    elif binary := shutil.which("notify-send"):
        command = [binary, "--", title, message]
    else:
        return
    try:
        subprocess.run(command, capture_output=True, timeout=5, check=False, env=child_environment())
    except (OSError, subprocess.TimeoutExpired):
        pass  # Notification delivery does not change the preparation result.


def run(folder: Path) -> int:
    regular_file(folder / "job.json", private=True)
    payload = json.loads((folder / "job.json").read_text())
    if project := payload.pop("project"):
        project["root"] = Path(project["root"])
        project = Project(**project)
    job = Preparation(**payload, project=project)
    if directory(job.provider, job.identifier) != folder:
        raise CrigError("Invalid preparation directory.")
    with FileLock(folder / "worker.lock", timeout=10, mode=0o600):
        record = read_status(folder)

        def progress(message: str):
            if record.get("step") == message:
                return
            record.update(phase="preparing", step=message)
            state.atomic_write(folder / "status.json", json.dumps(record))
            console.print(message, markup=False)

        try:
            progress("Waiting for the GPU")
            api = BrokerProvider(job.provider)
            if job.project:
                from cloudrig.workspace import Options, prepare

                with project_lock(job.project, timeout=10):
                    binding = receipt(job.project)
                    if binding is None or binding.id != job.identifier:
                        raise CrigError("The project no longer points to this instance.")
                    options = Options(
                        setup=job.setup,
                        open_editor=False,
                        timeout=job.timeout,
                        startup_script=job.startup_script,
                    )
                    prepare(job.project, api, binding, options, progress=progress)
                item = api.instance(job.identifier)
            else:
                item = wait_ready(
                    api,
                    job.identifier,
                    timeout=job.timeout,
                    startup_script=job.startup_script,
                    progress=progress,
                )
            if job.code_path is not None:
                progress("Opening VS Code")
                open_code(item, job.code_path)
            record.update(phase="ready", step="Ready", finished=time.time())
            state.atomic_write(folder / "status.json", json.dumps(record))
            notify("Cloudrig ready", f"{job.name} is ready.")
            return 0
        except Exception as error:
            message = (
                str(error) if isinstance(error, CrigError) else "Setup failed; diagnostic details withheld"
            )
            record.update(phase="failed", step=message, finished=time.time())
            state.atomic_write(folder / "status.json", json.dumps(record))
            console.print(message, markup=False)
            notify("Cloudrig setup failed", f"{job.name}: run crig status to inspect setup.")
            return 1


def show_status(target: str | None = None) -> None:
    root = state.config_dir() / "preparations"
    found = False
    if root.exists():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            record = read_status(folder)
            if not record or target and target not in {record["id"], record["name"]}:
                continue
            found = True
            elapsed = max(0, int(record.get("finished", time.time()) - record["started"]))
            console.print(
                f"{record['name']} ({record['provider']}/{record['id']}) · "
                f"{record['phase']} · {elapsed // 60}m {elapsed % 60}s",
                markup=False,
            )
            console.print(f"Step: {record['step']}\nLog: {folder / 'setup.log'}", markup=False)
    if not found:
        console.print("No background setup found.")


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1])))
