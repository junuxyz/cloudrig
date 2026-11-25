"""Rent Vast.ai GPUs, list them, open VS Code, stop, and delete."""

import argparse
import json
import subprocess
import sys

from rich.table import Table
from rich.text import Text

from cloudrig import __version__, connections, state, vast, vault
from cloudrig.demo import Demo
from cloudrig.models import Cancelled, CrigError, InstanceAction, Rental, sorted_offers
from cloudrig.style import gpu_label, named, total_vram, usd
from cloudrig.terminal import console, waiting


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="crig",
        description="Run cloud GPU experiments from your terminal — fast, simple, and agent-friendly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  crig                  Open the dashboard
  crig --gpu [QUERY]    Browse GPU offers
  crig --rented         List rented GPUs
  crig NAME code        Open VS Code
  crig NAME stop        Stop a GPU (PIN required)
  crig NAME destroy     Delete a GPU (PIN required)
  crig login            Connect your Vast.ai account
  crig logout           Remove saved local keys
""",
    )
    result.add_argument("words", nargs="*", metavar="ARG")
    result.add_argument("--version", action="version", version=f"cloudrig {__version__}")
    result.add_argument("--gpu", nargs="?", const="", metavar="QUERY", help="Browse GPU offers")
    result.add_argument("--rented", action="store_true", help="List rented GPUs")
    result.add_argument("--sort", choices=("price", "vram", "region", "gpus"), default="price")
    result.add_argument("--count", type=int, default=1, help="GPU count to search for")
    result.add_argument("--disk", type=int, default=40, help="Container disk in GB")
    result.add_argument("--plain", action="store_true", help="Print a table")
    result.add_argument("--json", action="store_true", help="Print data for scripts")
    result.add_argument("--demo", action="store_true", help="Preview without an account or cloud changes")
    result.add_argument("--connect-bg", metavar="ID", help=argparse.SUPPRESS)
    return result


def login() -> None:
    url = vast.API_KEY_URL
    console.print("\nConnect Vast.ai\n", style="crig.title")
    console.print("Get your API key here:")
    console.print(Text(url, style=f"underline link {url}"))
    console.print(
        "Cloudrig will mint a read-only key for browsing and a no-billing action key locked with your PIN.",
        style="crig.muted",
    )
    console.print("The key you paste is never saved.\n", style="crig.muted")

    raw_key = vault.read_tty_secret("Vast.ai API key (hidden): ").strip()
    if not raw_key:
        raise Cancelled("Cancelled.")

    pin = vault.read_tty_secret("Choose a PIN (4+ chars, hidden): ")
    if len(pin) < 4:
        raise CrigError("Use a PIN or passphrase with at least 4 characters.")
    confirm = vault.read_tty_secret("Confirm PIN (hidden): ")
    if pin != confirm:
        raise CrigError("PINs did not match. Nothing was saved.")

    vast.verify_key(raw_key)
    try:
        read_key, action_key = vast.mint_scoped_keys(raw_key)
    except vast._AuthDenied:
        vault.save_keys(None, raw_key, pin, scoped=False)
        console.print(
            "Connected to Vast.ai. Saved your key encrypted with your PIN "
            "(it did not have permission to mint a separate read-only key).",
            style="crig.ok",
        )
        return

    vault.save_keys(read_key, action_key, pin, scoped=True)
    console.print("Connected to Vast.ai with scoped read and PIN-locked action keys.", style="crig.ok")


def logout() -> None:
    vault.clear_keys()
    console.print("Removed saved local keys.", style="crig.ok")
    console.print(
        f"To revoke cloudrig keys on Vast.ai too, remove them at {vast.API_KEY_URL}", style="crig.muted"
    )


def print_rows(rows, args) -> None:
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], indent=2))
        return
    table = Table(box=None, header_style="crig.muted", highlight=False, padding=(0, 2))
    if args.rented:
        for name, justify in (
            ("name", "left"),
            ("status", "left"),
            ("gpu", "left"),
            ("price/hr", "right"),
            ("region", "left"),
            ("id", "right"),
        ):
            table.add_column(name, justify=justify)
        for row in rows:
            table.add_row(
                row.name, row.status, gpu_label(row.gpu, row.count), usd(row.hourly), row.region, row.id
            )
    else:
        for name, justify in (
            ("gpu", "left"),
            ("vram", "right"),
            ("price/hr", "right"),
            ("region", "left"),
            ("offer", "right"),
        ):
            table.add_column(name, justify=justify)
        for row in sorted_offers(rows, args.sort, args.disk):
            total = row.total(args.disk)
            rate_text = usd(row.hourly, partial=True) if total is None else usd(total)
            table.add_row(
                gpu_label(row.gpu, row.count),
                total_vram(row.vram, row.count),
                rate_text,
                row.region,
                row.id,
            )
    console.print(table)


def resolve(api, target: str):
    matches = [row for row in api.instances() if row.id == target or row.name == target]
    if len(matches) != 1:
        raise CrigError("Choose one exact instance name or ID from crig --rented.")
    return matches[0]


def rent(api, rental: Rental, args):
    if args.demo:
        console.print("Demo only. No GPU was rented.")
        return None
    identifier = api.rent(rental)
    console.print(Text(f"Rented {named(rental.name, identifier)}.", style="crig.ok"))
    return identifier


def spawn_background_connect(ident: str) -> None:
    """Wait for a booting GPU in a detached process and open VS Code once SSH is ready."""
    subprocess.Popen(
        [sys.executable, "-m", "cloudrig.cli", "--connect-bg", ident],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=state.child_environment(),
        start_new_session=True,
        close_fds=True,
    )


def action(api, item, name: str, args) -> None:
    if name not in {"code", "stop", "destroy"}:
        raise CrigError("Choose code, stop, or destroy. Run crig for the dashboard.")
    if args.demo:
        console.print(f"Demo: {name} {item.name}. No changes made.")
        return
    if name == "code":
        if not connections.has_keypair(item.id):
            _, pub = connections.ensure_keypair(item.id)
            api.attach_ssh_key(item.id, pub)
        opened, status = connections.try_connect_editor_now(api, item)
        if opened:
            console.print(f"Opened {named(item.name, item.id)} in VS Code.", style="crig.ok")
            return
        if vault.has_read_key() and isinstance(api, vast.VastClient):
            spawn_background_connect(item.id)
            console.print(
                f"{named(item.name, item.id)}: {status}. Opening VS Code in the background when ready.",
                style="crig.muted",
            )
            return
        with waiting() as progress:
            connections.connect_editor(api, item.id, progress=progress)
        return
    getattr(api, name)(item)
    console.print(
        f"{'Stop' if name == 'stop' else 'Delete'} request accepted for {named(item.name, item.id)}.",
        style="crig.ok",
    )
    if name == "stop":
        console.print("Storage charges may continue until the GPU is deleted.", style="crig.warn")


def dashboard(api, args) -> None:
    from cloudrig.tui import CloudrigApp

    def run_in_session(result: Rental | InstanceAction) -> tuple[str | None, str | None]:
        if isinstance(result, Rental):
            identifier = rent(api, result, args)
            if args.demo:
                return None, "Demo only. No GPU was rented."
            return identifier, f"Rented {named(result.name, identifier or '')}"
        action(api, result.instance, result.action, args)
        if args.demo:
            return result.instance.id, f"Demo: {result.action} {result.instance.name}"
        if result.action == "code":
            return result.instance.id, f"Opened {result.instance.name} in VS Code"
        verb = "Stop" if result.action == "stop" else "Delete"
        return result.instance.id, f"{verb} requested for {result.instance.name}"

    handoff = spawn_background_connect if (not args.demo and vault.has_read_key()) else None
    CloudrigApp(
        api,
        query=args.gpu or "",
        rented=args.rented,
        disk=args.disk,
        count=args.count,
        sort=args.sort,
        demo=args.demo,
        action_runner=run_in_session,
        handoff_connect=handoff,
    ).run()


def dispatch(args) -> None:
    if args.connect_bg:
        if vault.has_read_key():
            try:
                connections.connect_editor(vast.VastClient(), args.connect_bg)
            except CrigError:
                pass
        return
    if args.disk < 1 or args.count < 1:
        raise CrigError("Disk size and GPU count must be positive.")
    if args.words == ["login"]:
        if args.demo:
            raise CrigError("Demo mode does not change accounts.")
        login()
        return
    if args.words == ["logout"]:
        if args.demo:
            raise CrigError("Demo mode does not change accounts.")
        logout()
        return
    if args.words and (len(args.words) != 2 or args.words[1] not in {"code", "stop", "destroy"}):
        raise CrigError("Choose code, stop, or destroy. Run crig for the dashboard.")

    if not args.demo and not vault.has_credentials():
        if not args.plain and not args.json and sys.stdin.isatty():
            login()
            if not args.words and args.gpu is None and not args.rented:
                args.gpu = ""
        else:
            raise CrigError("Not logged in. Run crig login in your own terminal, or try crig --demo.")

    api = Demo() if args.demo else vast.VastClient()
    if args.words:
        target, command = args.words
        action(api, resolve(api, target), command, args)
        return

    args.rented = args.gpu is None
    if args.plain or args.json or not sys.stdin.isatty():
        rows = api.instances() if args.rented else api.offers(args.gpu or "", args.disk, args.count)
        print_rows(rows, args)
    else:
        dashboard(api, args)


def main() -> None:
    try:
        dispatch(parser().parse_intermixed_args(sys.argv[1:]))
    except Cancelled:
        console.print("Cancelled.")
        raise SystemExit(130) from None
    except CrigError as error:
        console.print(Text(f"crig: {error}", style="crig.error"))
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        console.print("\nInterrupted.")
        raise SystemExit(130) from None
    except OSError as error:
        console.print(Text(f"crig: {error.strerror or 'Local operation failed'}", style="crig.error"))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
