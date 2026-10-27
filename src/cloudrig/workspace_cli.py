"""Parse project commands and review their effects before executing them."""

import argparse
import json
import shlex
import sys
from dataclasses import asdict, replace

from rich.table import Table
from rich.text import Text

from cloudrig import state
from cloudrig.broker import BrokerProvider
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Offer, Rental, instance_id
from cloudrig.project import (
    SETTINGS,
    Binding,
    Phase,
    Project,
    candidates,
    forget_receipt,
    load_project,
    project_lock,
    receipt,
    save_receipt,
)
from cloudrig.terminal import console, prompt_confirm
from cloudrig.workspace import Options, launch, preflight
from cloudrig.workspace_tui import WorkspaceApp


def parser() -> argparse.ArgumentParser:
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


def show_plan(project: Project, rows: list[Offer], binding: Binding | None, setup: bool) -> None:
    console.print(Text(f"\n▲ cloudrig / {project.name}", style="bold #fab283"))
    console.print(
        Text(f"{project.vram:g}+ GB / GPU · {project.count} GPU · ${project.max_hourly:g}/hr ceiling")
    )
    if binding:
        console.print(Text(f"Existing workspace: {binding.id or 'submission unresolved'} · {binding.phase}"))
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


def init_project(project: Project, demo: bool) -> None:
    settings = (
        f"{key} = {json.dumps(getattr(project, key))}\n" for key in SETTINGS if getattr(project, key) != ""
    )
    content = "[workspace]\n" + "".join(settings) + "# hours estimates cost; it does not stop billing.\n"
    if demo:
        console.print(content, markup=False)
        return
    manifest = project.root / "crig.toml"
    try:
        with manifest.open("x") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise CrigError(
            "crig.toml already exists. Edit it directly; no settings were overwritten."
        ) from error
    console.print(Text(f"Created {manifest}. Review with crig plan; launch with crig up."))


def select_target(project: Project, api, args: argparse.Namespace) -> Rental | Binding | None:
    binding = receipt(project)
    if args.attach:
        if binding and binding.id:
            raise CrigError("This project is already bound. No allocation was changed.")
        item = api.instance(instance_id(args.attach))
        if not item.approved:
            api.approve(item.id)
        console.print(Text(f"Bind {project.name} to {item.name} ({item.id}), {item.gpu}, {item.region}."))
        prompt_confirm("Bind this instance and upload the project? Matching remote files can be overwritten.")
        return save_receipt(project, Binding(Phase.PREPARING, id=item.id))
    if binding:
        if binding.needs_upload:
            prompt_confirm("Finish initial upload? Local files can overwrite matching remote files.")
        return binding
    rows = candidates(project, api.offers(project.gpu, project.disk, project.count))
    if not rows:
        show_plan(project, rows, None, args.setup)
        raise CrigError("No matching offers. No rental was submitted.")
    if args.offer:
        offer = next((row for row in rows if row.id == args.offer), None)
        if offer is None:
            raise CrigError("The requested offer does not match this project's current requirements.")
        show_plan(project, [offer], None, args.setup)
        prompt_confirm("Upload this project to the reviewed GPU and prepare its workspace?")
    else:
        if not sys.stdin.isatty():
            raise CrigError("Review requires a terminal. Use crig plan --json for a read-only plan.")
        offer = WorkspaceApp(project, rows, setup=args.setup).run()
        if offer is None:
            return None
    return Rental(offer, project.rental_name, project.disk, project.image)


def dispatch(argv):
    args = parser().parse_args(argv)
    default_provider = "vastai" if args.demo else state.load().get("provider", "vastai")
    overrides = {key: getattr(args, key, None) for key in SETTINGS}
    project = load_project(args.path, default_provider=default_provider, **overrides)
    if args.command == "init":
        init_project(project, args.demo)
        return
    if args.command == "forget":
        if args.demo:
            console.print("Demo: would forget only the local project binding. No configuration was changed.")
            return
        with project_lock(project):
            if receipt(project) is None:
                raise CrigError("This project has no local binding to forget.")
            console.print(
                "Check crig --rented and your provider console first. Forgetting does not stop billing."
            )
            prompt_confirm("Forget the local binding? A future up may request a new allocation.")
            forget_receipt(project)
        console.print("Forgot the local binding. No provider operation was sent.")
        return
    if args.demo and project.provider != "vastai":
        raise CrigError("The workspace demo uses Vast.ai fixtures. Use --provider vastai.")
    api = Demo() if args.demo else BrokerProvider(project.provider)
    project = replace(project, image=project.image or api.default_image)
    binding = None if args.demo else receipt(project)
    if args.command == "down":
        if args.demo:
            console.print(
                "Demo: would stop this project's GPU. Storage charges may remain. No request was sent."
            )
            return
        if binding is None or binding.id is None:
            raise CrigError("This project has no confirmed allocation. Inspect crig --rented.")
        prompt_confirm(f"Stop workspace {binding.id}? Storage charges may remain.")
        api.stop(binding.id)
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
                    "binding": asdict(binding) if binding else None,
                    "offers": [row.to_dict() for row in rows],
                }
            )
        elif args.demo and args.command == "up" and not args.plain and sys.stdin.isatty():
            WorkspaceApp(project, rows, demo=True, setup=args.setup).run()
        else:
            show_plan(project, rows, binding, args.setup)
        return
    with project_lock(project):
        target = select_target(project, api, args)
        if target is not None:
            preflight(upload=isinstance(target, Rental) or target.needs_upload, editor=not args.no_code)
            options = Options(setup=args.setup, open_editor=not args.no_code, timeout=args.timeout)
            launch(project, api, target, options)
