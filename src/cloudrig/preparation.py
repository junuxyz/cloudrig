"""Prepare a rented GPU in a background process under the local user's account."""

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
from cloudrig.models import CrigError, instance_id, number, provider_name, safe_text
from cloudrig.project import Project, project_lock, receipt
from cloudrig.security import child_environment, private_directory, regular_file, safe_diagnostic
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


def statuses(provider: str) -> dict[str, dict]:
    """Read saved preparation progress without contacting a provider."""
    provider_name(provider)
    root = state.config_dir() / "preparations"
    if not root.exists():
        return {}
    private_directory(root)
    result = {}
    for folder in root.glob(f"{provider}-*"):
        private_directory(folder)
        record = read_status(folder)
        if record:
            identifier = instance_id(record["id"])
            if record["phase"] not in {"queued", "preparing", "ready", "failed", "interrupted"}:
                raise CrigError("Unknown preparation phase.")
            safe_text(record["name"])
            safe_text(record["step"])
            number(record["started"])
            if "finished" in record:
                number(record["finished"])
            for event in record.get("history", []):
                safe_text(event["step"])
                number(event["at"])
            if record["provider"] != provider or folder.name != f"{provider}-{identifier}":
                raise CrigError("Preparation record does not match its directory.")
            result[identifier] = record
    return result


def start(job: Preparation) -> Path:
    folder = directory(job.provider, job.identifier)
    private_directory(folder.parent)
    private_directory(folder)
    lock = folder / "worker.lock"
    regular_file(lock, private=True)
    payload = asdict(job)
    if job.project:
        payload["project"]["root"] = str(job.project.root)

    def existing() -> Path:
        regular_file(folder / "job.json", private=True)
        if json.loads((folder / "job.json").read_text()) != payload:
            raise CrigError(
                "A different setup is running on this GPU. Wait for it to finish, then try again."
            )
        return folder

    try:
        with FileLock(lock, timeout=0, mode=0o600):
            previous = read_status(folder)
            if previous.get("phase") == "queued":
                return existing()
            # A preparing worker holds this lock. Acquiring it proves the old worker ended,
            # even if the operating system has already reused its PID.
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
        return existing()
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


def read_job(folder: Path) -> Preparation:
    regular_file(folder / "job.json", private=True)
    payload = json.loads((folder / "job.json").read_text())
    if project := payload.pop("project", None):
        project["root"] = Path(project["root"])
        project = Project(**project)
    job = Preparation(**payload, project=project)
    if directory(job.provider, job.identifier) != folder:
        raise CrigError("Invalid preparation directory.")
    return job


def run(folder: Path) -> int:
    job = read_job(folder)
    with FileLock(folder / "worker.lock", timeout=10, mode=0o600):
        record = read_status(folder)

        def progress(message: str, *, phase: str = "preparing"):
            if record.get("step") == message and record.get("phase") == phase:
                return
            timestamp = time.time()
            record.update(phase=phase, step=message)
            record.setdefault("history", []).append({"step": message, "at": timestamp})
            if phase in {"ready", "failed"}:
                record["finished"] = timestamp
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
            progress("Ready", phase="ready")
            notify("Cloudrig ready", f"{job.name} is ready.")
            return 0
        except Exception as error:
            if isinstance(error, CrigError):
                message = str(error)
            else:
                message = "Setup failed. Include the diagnostic below in a bug report."
                record["diagnostic"] = safe_diagnostic(error)
                console.print(json.dumps(record["diagnostic"]), markup=False)
            progress(message, phase="failed")
            notify("Cloudrig setup failed", f"{job.name}: open crig to inspect setup.")
            return 1


def show_status(target: str | None = None) -> None:
    root = state.config_dir() / "preparations"
    found = False
    shown = set()
    if root.exists():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            record = read_status(folder)
            if not record or target and target not in {record["id"], record["name"]}:
                continue
            found = True
            shown.add((record["provider"], record["id"]))
            elapsed = max(0, int(record.get("finished", time.time()) - record["started"]))
            console.print(
                f"{record['name']} ({record['provider']}/{record['id']}) · "
                f"{record['phase']} · {elapsed // 60}m {elapsed % 60}s",
                markup=False,
            )
            console.print(f"Step: {record['step']}\nLog: {folder / 'setup.log'}", markup=False)
            if diagnostic := record.get("diagnostic"):
                console.print(f"Diagnostic: {json.dumps(diagnostic)}", markup=False)
    for binding in state.load().get("projects", {}).values():
        if not binding.get("root"):
            continue
        name = Path(binding["root"]).name
        if target and target not in {name, binding.get("id"), binding["root"]}:
            continue
        if (binding["provider"], binding.get("id")) in shown:
            continue
        found = True
        console.print(
            f"Project {name} · {binding['provider']}/{binding.get('id') or 'unknown ID'} · "
            f"{binding['phase']}\nLocal folder: {binding['root']}",
            markup=False,
        )
        if not binding.get("id"):
            console.print(
                "Check the provider console before retrying. Run crig up in this folder for recovery steps."
            )
    if not found:
        console.print("No saved workspace or background setup found.")


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1])))
