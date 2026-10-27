import argparse
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from cloudrig import __version__, state
from cloudrig.connections import (
    full_ssh,
    install_alias,
    open_code,
    run,
    ssh_command,
    sync_command,
    wait_ready,
)
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Instance, InstanceAction, Rental
from cloudrig.providers import connect
from cloudrig.tui import CloudrigApp, rate, sorted_offers

console = Console(highlight=False)
ACTIONS = {"ssh", "code", "exec", "agent", "push", "pull", "start", "stop", "destroy", "approve"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="crig",
        description="Find a GPU. Rent it. Get to work.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""First run:
  crig --set vastai          Choose vastai or runpod
  crig login                Connect account; fresh sudo; credentials stay root-only
  crig --gpu 5090            Browse, sort, review, and rent
  crig --rented              Connect to or manage your instances

Everyday:
  crig training code .       Open /workspace in remote VS Code
  crig training ssh          Connect with a stable SSH alias
  crig training push ./ /workspace/project/
  crig training pull /workspace/outputs/ ./outputs/
  crig training exec -- nvidia-smi

Try without an account:
  crig --demo --gpu 5090
  crig --demo --rented

Run crig doctor to check local tools.
""",
    )
    result.add_argument("words", nargs="*", metavar="ARG")
    result.add_argument("--version", action="version", version=f"cloudrig {__version__}")
    result.add_argument("--set", choices=state.PROVIDERS, dest="set_provider", help="Save default provider")
    result.add_argument("--provider", choices=state.PROVIDERS, help="Override provider for this invocation")
    result.add_argument("--gpu", metavar="QUERY", help="Search GPUs by case-insensitive substring")
    result.add_argument(
        "--rented", action="store_true", help="Show all rented instances in the selected account"
    )
    result.add_argument("--sort", choices=("price", "vram", "region", "gpus"), default="price")
    result.add_argument("--count", type=int, default=1, help="Minimum GPU count (Vast); exact count (RunPod)")
    result.add_argument("--disk", type=int, default=40, help="Container disk in GB (default: 40)")
    result.add_argument("--plain", action="store_true", help="Print a table instead of the interactive UI")
    result.add_argument("--json", action="store_true", help="Print normalized data for scripts")
    result.add_argument(
        "--demo", action="store_true", help="Use sample data; no API, SSH, or configuration writes"
    )
    result.add_argument(
        "--rent", metavar="OFFER_ID", help="Rent a specific result from --gpu; review before confirming"
    )
    result.add_argument("--name", help="Workspace name for --rent")
    result.add_argument("--image", help="Override the default container image")
    result.add_argument(
        "--yes",
        action="store_true",
        help="Skip the local stop prompt; sudo and privileged confirmations are never skipped",
    )
    result.add_argument(
        "--code",
        nargs="?",
        const="/workspace",
        metavar="REMOTE_PATH",
        help="Open VS Code after rental (default: /workspace)",
    )
    result.add_argument(
        "--identity",
        type=Path,
        help="Deprecated: private-key paths are rejected; keys are managed by the protected helper",
    )
    result.add_argument("--send-to-local", nargs=2, metavar=("REMOTE", "LOCAL"), help="Download with rsync")
    result.add_argument("--send-to-cloud", nargs=2, metavar=("LOCAL", "REMOTE"), help="Upload with rsync")
    result.add_argument(
        "--dry-run", action="store_true", help="Preview file transfer without modifying files"
    )
    result.add_argument(
        "--clipboard", action="store_true", help="Deprecated: direct SSH/key imports are disabled"
    )
    result.add_argument("--timeout", type=int, default=300, help="Seconds to wait for SSH after renting")
    return result


def intro():
    console.print("\n[bold #fab283]▲  cloudrig[/]  [dim]Find a GPU. Get to work.[/]\n")
    console.print("Choose your cloud first.\n")
    console.print("  [bold]crig --set vastai[/]     Vast.ai marketplace")
    console.print("  [bold]crig --set runpod[/]     RunPod GPU cloud\n")
    console.print("Then run [bold]crig[/] to connect your account, or try [bold]crig --demo --gpu 5090[/].\n")


def prompt_confirm(message: str, yes: bool = False):
    if yes:
        return
    if not sys.stdin.isatty():
        raise CrigError("Confirmation requires a terminal. Use --yes only for an intended operation.")
    if not Confirm.ask(Text(message), default=False):
        raise CrigError("Cancelled. No operation was submitted.")


def login(provider: str, explicit: Path | None = None):
    if explicit:
        raise CrigError("Private-key paths are no longer accepted. Keys are managed only by the root helper.")
    from cloudrig.broker import admin

    admin("login", provider)
    console.print(f"Connected to {provider}. Credentials are stored by the root-owned helper.")


def print_rows(rows, args):
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], indent=2))
        return
    table = Table(box=None, padding=(0, 2), header_style="dim", highlight=False)
    if args.rented:
        for name in ("NAME", "GPU", "STATUS", "USD / HR", "REGION", "ID"):
            table.add_column(name, no_wrap=name in {"NAME", "ID"})
        for row in rows:
            table.add_row(
                Text(row.name),
                Text(f"{row.gpu} ×{row.count}"),
                Text(row.status),
                f"${row.hourly:.3f}",
                Text(row.region),
                Text(row.id),
            )
    else:
        for name in ("GPU", "VRAM / GPU", "USD / HR", "REGION", "OFFER"):
            table.add_column(name)
        for row in rows:
            table.add_row(
                Text(row.title), f"{row.vram:g} GB", rate(row, args.disk), Text(row.region), Text(row.id)
            )
    console.print(table)
    if not rows:
        console.print("[dim]No matches. Try a different query or refresh later.[/]")
    elif not args.rented:
        console.print(f"\n[dim]{len(rows)} offers · {args.disk} GB disk · + means storage is extra[/]")


def resolve(api, target: str | None) -> Instance:
    rows = api.instances()
    if target:
        exact = [row for row in rows if row.id == target or row.name == target]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise CrigError(
                "Several instances share that name. Use the exact instance ID from crig --rented."
            )
        raise CrigError(f"No instance named {target!r}. Run crig --rented.")
    active = state.load().get("active", {}).get(api.name)
    matches = [row for row in rows if row.id == active]
    if matches:
        return matches[0]
    if len(rows) == 1:
        return rows[0]
    raise CrigError("Choose an instance: crig NAME ssh. List names with crig --rented.")


def rent(api, rental: Rental, args):
    if args.demo:
        console.print("Demo rental reviewed. No resource or credential was changed.")
        return
    identifier = api.rent(rental)
    state.remember_instance(api.name, identifier)
    console.print(Text(f"Rented {rental.name} · {identifier}", style="green"))
    with console.status("Waiting for the approved SSH signer…"):
        instance = wait_ready(api, identifier, timeout=args.timeout)
    name = install_alias(instance)
    if args.code:
        open_code(instance, None, "/workspace" if args.code == "." else args.code)
    elif sys.stdin.isatty() and not args.yes:
        run(ssh_command(name))


def finish(api, result, args):
    # The TUI can switch between market and workspaces, so either result may come back.
    if isinstance(result, Rental):
        rent(api, result, args)
    elif isinstance(result, InstanceAction):
        action(api, result.instance, result.action, [], args)


def action(api, item: Instance, name: str, paths: list[str], args):
    if args.demo:
        console.print(Text(f"Demo: {name} → {item.name}. No SSH, files, or cloud resources were changed."))
        return
    if name in {"stop", "destroy", "start", "approve"}:
        message = f"{name.capitalize()} {item.name} ({item.id})?"
        if name == "destroy":
            console.print(
                "[yellow]Deleting removes this instance and its local files. Pull your outputs first.[/]"
            )
        elif name == "stop":
            console.print("Stopping may retain storage charges and may release GPU capacity.")
        elif name == "start":
            console.print("Starting resumes billable compute. The root helper will check and quote it.")
        if name == "stop":
            prompt_confirm(message, args.yes)
        getattr(api, name)(item.id)
        console.print(Text(f"✓ {name.capitalize()} request accepted for {item.name}.", style="green"))
        return
    identity = None
    fresh = api.instance(item.id)
    if fresh.status != "running":
        raise CrigError(f"Instance is {fresh.status}. Start it with crig {item.id} start.")
    state.remember_instance(api.name, item.id)
    if name == "code":
        remote = paths[0] if paths else "/workspace"
        open_code(fresh, identity, "/workspace" if remote == "." else remote)
    elif name in {"push", "pull"}:
        if len(paths) != 2:
            if not sys.stdin.isatty():
                raise CrigError("Specify both source and destination paths. See crig --help.")
            if name == "push":
                paths = [
                    Prompt.ask("Local source", default="./"),
                    Prompt.ask("Remote destination", default="/workspace/"),
                ]
            else:
                paths = [
                    Prompt.ask("Remote source", default="/workspace/outputs/"),
                    Prompt.ask("Local destination", default="./outputs/"),
                ]
        local, remote = paths if name == "push" else paths[::-1]
        argv = sync_command(fresh, identity, local, remote, name == "push", args.dry_run)
        console.print(Text(shlex.join(argv), style="dim"))
        run(argv)
    else:
        alias_name = install_alias(fresh, identity)
        if name == "ssh":
            run(ssh_command(alias_name))
            console.print(f"Instance still rented. Stop: crig {item.id} stop", markup=False)
        elif name == "exec":
            full_ssh(fresh)
            if not paths:
                raise CrigError("Specify a command: crig NAME exec -- nvidia-smi")
            run(ssh_command(alias_name, shlex.join(paths)))
        elif name == "agent":
            full_ssh(fresh)
            agent = (
                paths[0]
                if paths
                else Prompt.ask("Remote agent", choices=["claude", "codex"], default="claude")
            )
            if agent not in {"claude", "codex"}:
                raise CrigError(
                    "Choose claude or codex. Install and authenticate it on the remote machine first."
                )
            command = f"cd /workspace && {shlex.join([agent, *paths[1:]])}"
            run(ssh_command(alias_name, command, tty=True))


def doctor():
    from cloudrig.broker import HELPER, trusted_root_path

    table = Table(box=None, header_style="dim")
    table.add_column("TOOL")
    table.add_column("STATUS")
    table.add_column("PURPOSE / SETUP")
    for tool, purpose in [
        ("ssh", "Connect to GPU instances"),
        ("rsync", "File transfer requires upstream rsync 3.5.0+"),
        ("code", "Install VS Code shell command and Remote - SSH extension"),
    ]:
        path = shutil.which(tool)
        table.add_row(tool, Text(path or "Missing", style="green" if path else "yellow"), purpose)
    try:
        trusted_root_path(HELPER)
        helper_status = Text(str(HELPER), style="green")
    except (OSError, CrigError):
        helper_status = Text("Missing or unprotected", style="yellow")
    table.add_row(
        "root helper", helper_status, "Install with scripts/install-admin.sh; verify with crig self-test"
    )
    console.print(table)
    console.print(
        "Credentials require the root-owned service. No environment/keychain credential fallback is used."
    )
    console.print("No provider CLI is required. Run crig --demo --gpu 5090 to preview the UI.")


def dispatch(args):
    if args.count < 1 or args.disk < 1 or args.timeout < 1:
        raise CrigError("--count, --disk, and --timeout must be positive.")
    if args.send_to_cloud and args.send_to_local:
        raise CrigError("Choose one transfer direction per invocation.")
    words = args.words
    if words and words[0] == "self-test":
        from cloudrig.broker import admin

        admin("self-test")
        console.print("Root-owned credential permissions and non-interactive sudo checks passed.")
        return
    if words and words[0] == "doctor":
        doctor()
        return
    if args.set_provider:
        if args.demo:
            raise CrigError("--demo never changes configuration. Remove --demo to select a provider.")
        state.update(provider=args.set_provider)
        console.print(f"[green]✓[/] Default cloud: {args.set_provider}. Run crig to connect your account.")
        return
    if not args.demo and not state.load().get("provider") and not args.provider:
        intro()
        if words or args.gpu is not None or args.rented:
            raise CrigError("Select a provider before this command.")
        return
    provider = args.provider or ("vastai" if args.demo else state.provider_or_error())
    command = words[0] if words else ""
    if command in {"login", "logout", "setup"} and args.demo:
        raise CrigError("--demo does not access credentials or register keys.")
    if command == "login":
        login(provider, args.identity)
        return
    if command == "logout":
        raise CrigError(
            "Revoke the key in the provider console. Stored credential changes require crig login and sudo."
        )
    if args.identity or args.clipboard:
        raise CrigError("Unprotected private-key/clipboard connections are disabled. Use crig NAME approve.")
    try:
        api = Demo() if args.demo else connect(provider)
    except CrigError:
        if not words and args.gpu is None and not args.rented and sys.stdin.isatty():
            login(provider, args.identity)
            return
        raise
    if command == "setup":
        raise CrigError("Install the protected helper with scripts/install-admin.sh, then use crig login.")
    if command == "billing":
        if args.demo:
            console.print("Demo: billing stays with your GPU provider. No browser was opened.")
        else:
            console.print(api.billing_url, markup=False)
            if not args.plain and sys.stdin.isatty():
                run(["open" if sys.platform == "darwin" else "xdg-open", api.billing_url])
        return
    if (
        args.gpu is None
        and len(words) == 1
        and any(char.isdigit() for char in command)
        and command not in ACTIONS
    ):
        # GPU shorthand is only used if no exact instance ID/name matches.
        if not any(row.id == command or row.name == command for row in api.instances()):
            args.gpu, words, command = command, [], ""
    if args.gpu is not None:
        if args.rented:
            raise CrigError("Choose GPU search or your rented instances, not both.")
        if args.rent or args.plain or args.json or not sys.stdin.isatty():
            offers = sorted_offers(api.offers(args.gpu, args.disk, args.count), args.sort, args.disk)
            if args.rent:
                offer = next((row for row in offers if row.id == args.rent), None)
                if not offer:
                    raise CrigError("The selected offer is not in the search results. Refresh the search.")
                name = args.name or f"gpu-{offer.id.split('|')[-1].lower()}"
                if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}", name):
                    raise CrigError(
                        "Use a workspace name with letters, numbers, '-' or '_', up to 63 characters."
                    )
                rental = Rental(offer, name, args.disk, args.image or api.default_image)
                if not rental.image or any(char.isspace() for char in rental.image):
                    raise CrigError("Enter a valid container image without spaces.")
                console.print(Text(f"{offer.title} · {offer.region} · {rate(offer, args.disk)}/hr estimated"))
                console.print(Text(f"Name: {name} · Disk: {args.disk} GB · Image: {rental.image}"))
                console.print(
                    "Storage extra where marked '+'. Billing starts on rental; data transfer may cost extra."
                )
                prompt_confirm("Rent this GPU?", args.yes)
                rent(api, rental, args)
            else:
                print_rows(offers, args)
            return
        result = CloudrigApp(
            api, args.gpu, disk=args.disk, count=args.count, sort=args.sort, demo=args.demo, image=args.image
        ).run()
        finish(api, result, args)
        return
    if args.rent:
        raise CrigError("Use --rent with --gpu QUERY so the current offer and price can be reviewed.")
    if args.send_to_cloud or args.send_to_local:
        item = resolve(api, words[0] if words else None)
        direction = "push" if args.send_to_cloud else "pull"
        action(api, item, direction, args.send_to_cloud or args.send_to_local, args)
        return
    if words:
        if command in ACTIONS:
            target, selected, paths = None, command, words[1:]
        else:
            target, selected, paths = words[0], words[1] if len(words) > 1 else "ssh", words[2:]
        if selected not in ACTIONS:
            raise CrigError(
                "Unknown action. Use ssh, code, exec, agent, push, pull, start, stop, or destroy."
            )
        item = resolve(api, target)
        action(api, item, selected, paths, args)
        return
    args.rented = True
    if args.plain or args.json or not sys.stdin.isatty():
        print_rows(api.instances(), args)
    else:
        result = CloudrigApp(api, rented=True, demo=args.demo, image=args.image).run()
        finish(api, result, args)


def main():
    try:
        # Preserve remote command options after '--'; they belong to the remote executable.
        argv = sys.argv[1:]
        remote = []
        if "--" in argv:
            index = argv.index("--")
            remote, argv = argv[index + 1 :], argv[:index]
        args = parser().parse_intermixed_args(argv)
        args.words.extend(remote)
        dispatch(args)
    except CrigError as error:
        console.print(Text(f"crig: {error}", style="#edb19e"), soft_wrap=True)
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        console.print("\nCancelled. A submitted rental may still be running; check crig --rented.")
        raise SystemExit(130) from None
    except OSError as error:
        console.print(
            Text(
                f"crig: Local operation failed: {error.strerror or 'check file permissions and tools'}",
                style="#edb19e",
            )
        )
        raise SystemExit(1) from None
    except Exception:
        console.print(
            "crig: Unexpected failure. Diagnostic details were withheld to protect credentials. "
            "No automatic retry was made. Check crig --rented before repeating a rental."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
