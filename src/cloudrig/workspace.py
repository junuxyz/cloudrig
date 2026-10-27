"""Rent once, prepare a project, and reconnect without overwriting remote edits."""

import argparse
import json
import shlex
import subprocess
import sys

from filelock import FileLock, Timeout
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cloudrig import state
from cloudrig.connections import (
    check_rsync,
    install_alias,
    open_code,
    require,
    run,
    ssh_command,
    sync_command,
    wait_ready,
)
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Rental, instance_id
from cloudrig.project import (
    candidates,
    forget_receipt,
    load_project,
    receipt,
    save_receipt,
    with_default_image,
)
from cloudrig.providers import connect
from cloudrig.security import child_environment, private_directory, regular_file
from cloudrig.workspace_tui import DemoReadyApp, WorkspaceApp

console = Console(highlight=False)


def parser():
    result = argparse.ArgumentParser(prog="crig", description="Open this project on a cloud GPU.")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "plan", "up", "down", "forget"):
        cmd = commands.add_parser(name)
        cmd.add_argument("path", nargs="?", default=".", help="Local project directory")
        cmd.add_argument("--demo", action="store_true", help="Fictional data; no cloud or config writes")
        if name == "init":
            continue
        cmd.add_argument("--provider", choices=state.PROVIDERS)
        if name in {"down", "forget"}:
            continue
        cmd.add_argument("--gpu", help="Optional GPU name substring, such as 5090 or A100")
        cmd.add_argument("--vram", type=float, help="Minimum VRAM per GPU in GB, default 24")
        cmd.add_argument("--count", type=int, help="Exact GPU count, default 1")
        cmd.add_argument("--disk", type=int, help="Container disk in GB, default 40")
        cmd.add_argument(
            "--max-hourly", type=float, help="Search limit for quoted compute + disk in USD, default 1"
        )
        cmd.add_argument(
            "--hours", type=float, help="Session estimate only; default 4, no automatic shutdown"
        )
        cmd.add_argument("--region", help="Location substring filter, such as JP. No inferred latency.")
        cmd.add_argument("--image", help="Container image override")
        cmd.add_argument("--plain", action="store_true", help="Print plan without the interactive review")
        cmd.add_argument("--json", action="store_true", help="Print plan as JSON; never rent")
        cmd.add_argument("--offer", help="Review this exact matching offer in a terminal")
        cmd.add_argument("--attach", help="Recover by binding an existing approved instance ID; never rent")
        cmd.add_argument(
            "--setup", action="store_true", help="Run detected uv/pip install remotely on first setup"
        )
        cmd.add_argument("--no-code", action="store_true", help="Prepare without opening VS Code")
        cmd.add_argument("--timeout", type=int, default=300, help="SSH readiness timeout in seconds")
    return result


def preflight(no_code):
    require("ssh")
    check_rsync()
    if not no_code:
        try:
            result = subprocess.run(
                [require("code"), "--list-extensions"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
                env=child_environment(),
            )
        except subprocess.TimeoutExpired as error:
            raise CrigError("VS Code did not respond. Check its shell command with crig doctor.") from error
        if result.returncode or "ms-vscode-remote.remote-ssh" not in result.stdout.lower().splitlines():
            raise CrigError(
                "Install Remote - SSH first: code --install-extension ms-vscode-remote.remote-ssh"
            )


def show_plan(project, rows, binding, setup):
    console.print(Text(f"\n▲ cloudrig / {project.name}", style="bold #fab283"))
    console.print(
        Text(f"{project.vram:g}+ GB / GPU · {project.count} GPU · ${project.max_hourly:g}/hr ceiling")
    )
    if binding:
        console.print(
            Text(f"Existing workspace: {binding.get('id', 'submission unresolved')} · {binding['phase']}")
        )
        console.print("crig up reconnects to this allocation. It does not rent a replacement.")
    table = Table(box=None)
    for column in ("OFFER", "GPU", "GB / GPU", "LOCATION", "USD / HR", f"{project.hours:g} H EST."):
        table.add_column(column)
    for row in rows:
        total = row.total(project.disk)
        table.add_row(
            row.id,
            row.title,
            f"{row.vram:g}",
            Text(row.region),
            f"${total:.3f}",
            f"${total * project.hours:.2f}",
        )
    console.print(table)
    if not rows and not binding:
        console.print("No complete quotes match. Change --vram, --max-hourly, --region, or --gpu.")
    console.print(Text(f"Remote: {project.remote}\nImage: {project.image}"))
    console.print("First up: SSH → upload → CUDA check → VS Code. Next up: reconnect without upload.")
    if setup and project.setup:
        console.print(Text(f"Remote dependency install: {shlex.join(project.setup)}"))
    console.print("Hours are an estimate, not a shutdown timer. Transfer and later storage costs are extra.")


def prepare(project, api, identifier, args, *, reconnect):
    console.print(
        Text(f"Workspace instance: {identifier}. Recovery: crig up {shlex.quote(str(project.root))}")
    )
    with console.status("Checking the approved SSH connection…"):
        item = wait_ready(api, identifier, timeout=args.timeout)
    name = install_alias(item)
    if not reconnect:
        console.print("Uploading the project. Credentials, .git, .venv, and caches are excluded.")
        run(ssh_command(name, f"mkdir -p -- {shlex.quote(project.remote)}"))
        # A previous interrupted upload may have copied only part of the project. Re-running rsync is safe
        # until the first complete setup. Ready workspaces never upload implicitly.
        run(sync_command(item, None, str(project.root) + "/", project.remote + "/", True))
        save_receipt(project, id=identifier, phase="uploaded")
    saved = receipt(project) or {}
    python = saved.get("python", "python")
    if args.setup and project.setup and not saved.get("setup_complete"):
        command = f"cd {shlex.quote(project.remote)} && {shlex.join(project.setup)}"
        console.print(Text(f"Installing project dependencies: {shlex.join(project.setup)}"))
        run(ssh_command(name, command))
        python = ".venv/bin/python" if project.setup[0] == "uv" else "python"
        save_receipt(project, setup_complete=True, python=python)
    probe = (
        "import torch; "
        "assert torch.cuda.is_available(), 'PyTorch cannot access CUDA'; "
        "print(torch.cuda.get_device_name(0))"
    )
    console.print("Checking the NVIDIA driver and PyTorch CUDA access…")
    run(
        ssh_command(
            name, f"cd {shlex.quote(project.remote)} && nvidia-smi -L && {python} -c {shlex.quote(probe)}"
        )
    )
    save_receipt(project, id=identifier, phase="ready")
    if not args.no_code:
        open_code(item, None, project.remote)
    console.print(Text(f"Ready: {project.remote}", style="green"))
    console.print(Text(f"Stop compute: crig down {shlex.quote(str(project.root))}"))
    console.print("Remote edits stay on this instance. Pull your outputs before deleting it.")


def launch(project, api, args):
    from cloudrig.cli import prompt_confirm

    previous = receipt(project)
    if args.attach:
        if previous and previous.get("id"):
            raise CrigError("This project is already bound. No allocation was changed.")
        identifier = instance_id(args.attach)
        item = api.instance(identifier)
        if not item.approved:
            raise CrigError(
                f"Authorize the instance first: crig --provider {project.provider} {identifier} approve"
            )
        console.print(Text(f"Bind {project.name} to {item.name} ({identifier}), {item.gpu}, {item.region}."))
        prompt_confirm("Bind this instance and upload the project? Matching remote files can be overwritten.")
        save_receipt(project, id=identifier, phase="preparing")
        previous = receipt(project)
    if previous:
        identifier = previous.get("id")
        if not identifier:
            # Neither retries nor an empty/stale provider list prove that a paid submission failed.
            raise CrigError(
                "The previous rental submission is unresolved. "
                "Inspect crig --rented and the provider console. "
                "Recover with crig up --attach ID. No retry was made."
            )
        item = api.instance(identifier)
        if item.status in {"stopped", "exited"}:
            console.print(Text(f"Resume {item.name} ({identifier}). Fresh sudo will quote the current rate."))
            api.start(identifier)
        elif item.status not in {"running", "loading", "creating", "starting", "initializing"}:
            raise CrigError(
                f"Workspace {identifier} is {item.status}. Inspect crig --rented. No replacement was rented."
            )
        if previous["phase"] not in {"uploaded", "ready"} and not args.attach:
            prompt_confirm("Finish initial upload? Local files can overwrite matching remote files.")
        prepare(project, api, identifier, args, reconnect=previous["phase"] in {"uploaded", "ready"})
        return
    rows = candidates(project, api.offers(project.gpu, project.disk, project.count))
    if not rows:
        show_plan(project, rows, None, args.setup)
        raise CrigError("No matching offers. No rental was submitted.")
    if args.offer:
        offer = next((row for row in rows if row.id == args.offer), None)
        if not offer:
            raise CrigError("The requested offer does not match this project's current requirements.")
        show_plan(project, [offer], None, args.setup)
        prompt_confirm("Upload this project to the reviewed GPU and prepare its workspace?")
    else:
        if not sys.stdin.isatty():
            raise CrigError("Review requires a terminal. Use crig plan --json for a read-only plan.")
        offer = WorkspaceApp(project, rows, setup=args.setup).run()
        if offer is None:
            return
    # Written before the paid call, then bound to the ID before SSH. A crash cannot silently rent twice.
    rental = Rental(offer, project.rental_name, project.disk, project.image)
    save_receipt(project, phase="submitting", offer=offer.id)
    identifier = instance_id(api.rent(rental))
    save_receipt(project, id=identifier, phase="preparing")
    prepare(project, api, identifier, args, reconnect=False)


def dispatch(argv):
    args = parser().parse_args(argv)
    keys = ("provider", "gpu", "vram", "count", "disk", "max_hourly", "hours", "region", "image")
    overrides = {key: getattr(args, key, None) for key in keys}
    if not args.demo and overrides["provider"] is None:
        # Manifest values take precedence over the global provider preference.
        initial = load_project(args.path)
        if not (initial.root / "crig.toml").exists():
            overrides["provider"] = state.load().get("provider", "vastai")
    project = load_project(args.path, **overrides)
    if args.command == "init":
        manifest = project.root / "crig.toml"
        content = (
            f"[workspace]\nprovider = {json.dumps(project.provider)}\nvram = 24\ncount = 1\ndisk = 40\n"
            'max_hourly = 1.0\nhours = 4\n# gpu = "5090"\n# region = "JP"\n'
            "# hours estimates cost; it does not stop billing.\n"
        )
        if args.demo:
            console.print(content, markup=False)
            return
        try:
            with manifest.open("x") as stream:
                stream.write(content)
        except FileExistsError as error:
            raise CrigError(
                "crig.toml already exists. Edit it directly; no settings were overwritten."
            ) from error
        console.print(Text(f"Created {manifest}. Review with crig plan; launch with crig up."))
        return
    if args.command == "forget":
        if args.demo:
            console.print("Demo: would forget only the local project binding. No configuration was changed.")
            return
        from cloudrig.cli import prompt_confirm

        root = state.config_dir()
        private_directory(root)
        lock_path = root / f"project-{project.key}.lock"
        regular_file(lock_path)
        try:
            with FileLock(lock_path, timeout=0, mode=0o600):
                if not receipt(project):
                    raise CrigError("This project has no local binding to forget.")
                console.print(
                    "Check crig --rented and your provider console first. Forgetting does not stop billing."
                )
                prompt_confirm("Forget the local binding? A future up may request a new allocation.")
                forget_receipt(project)
        except Timeout as error:
            raise CrigError("Another crig up is using this binding. Wait for it to finish.") from error
        console.print("Forgot the local binding. No provider operation was sent.")
        return
    api = Demo() if args.demo else connect(project.provider)
    project = with_default_image(project, api.default_image)
    if args.demo and project.provider != "vastai":
        raise CrigError("The workspace demo uses Vast.ai fixtures. Use --provider vastai.")
    binding = None if args.demo else receipt(project)
    if args.command == "down":
        if args.demo:
            console.print(
                "Demo: would stop this project's GPU. Storage charges may remain. No request was sent."
            )
            return
        if not binding or not binding.get("id"):
            raise CrigError("This project has no confirmed allocation. Inspect crig --rented.")
        from cloudrig.cli import prompt_confirm

        prompt_confirm(f"Stop workspace {binding['id']}? Storage charges may remain.")
        api.stop(binding["id"])
        console.print("Stop request accepted. The project binding remains for the next crig up.")
        return
    if args.timeout < 1:
        raise CrigError("--timeout must be positive.")
    if args.command == "plan" or args.plain or args.json or args.demo:
        rows = candidates(project, api.offers(project.gpu, project.disk, project.count))
        if args.json:
            console.print_json(
                data={
                    "project": project.to_dict(),
                    "binding": binding,
                    "offers": [row.to_dict() for row in rows],
                }
            )
        elif args.demo and args.command == "up" and not args.plain and sys.stdin.isatty():
            offer = WorkspaceApp(project, rows, demo=True, setup=args.setup).run()
            if offer:
                DemoReadyApp(project, offer).run()
        else:
            show_plan(project, rows, binding, args.setup)
        return
    preflight(args.no_code)
    root = state.config_dir()
    private_directory(root)
    lock_path = root / f"project-{project.key}.lock"
    regular_file(lock_path)
    try:
        with FileLock(lock_path, timeout=0, mode=0o600):
            launch(project, api, args)
    except Timeout as error:
        raise CrigError("Another crig up is working on this project. Wait for it to finish.") from error
