"""Rent GPUs, list them, stop them, and delete them."""

import argparse
import json
import sys

from rich.table import Table
from rich.text import Text

from cloudrig import __version__, state
from cloudrig.broker import API_KEY_URLS, BrokerProvider, admin, check_service
from cloudrig.connections import connect_editor
from cloudrig.demo import Demo
from cloudrig.models import CrigError, InstanceAction, Rental
from cloudrig.security import safe_diagnostic
from cloudrig.terminal import console, prompt_confirm, waiting
from cloudrig.tui import CloudrigApp, sorted_offers


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="crig",
        description="Rent a cloud GPU, open VS Code, stop, and delete.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Start here:
  crig                  Open the dashboard
  crig --gpu            Browse GPUs to rent
  crig NAME code        Open VS Code
  crig NAME stop        Stop a GPU
  crig NAME destroy     Delete a GPU

Account setup: crig setup. Updates: crig update.
""",
    )
    result.add_argument("words", nargs="*", metavar="ARG")
    result.add_argument("--version", action="version", version=f"cloudrig {__version__}")
    result.add_argument("--gpu", nargs="?", const="", metavar="QUERY", help="Browse GPU offers")
    result.add_argument("--rented", action="store_true", help="List rented GPUs")
    result.add_argument("--provider", choices=state.PROVIDERS, help="Choose the provider for this command")
    result.add_argument("--sort", choices=("price", "vram", "region", "gpus"), default="price")
    result.add_argument("--count", type=int, default=1, help="GPU count to search for")
    result.add_argument("--disk", type=int, default=40, help="Container disk in GB")
    result.add_argument("--plain", action="store_true", help="Print a table")
    result.add_argument("--json", action="store_true", help="Print data for scripts")
    result.add_argument("--demo", action="store_true", help="Preview without an account or cloud changes")
    return result


def login(provider: str):
    url = API_KEY_URLS[provider]
    console.print("\nNo API key yet? Create one here:")
    console.print(Text(url, style=f"underline link {url}"))
    console.print("Copy your key, then paste it into the hidden API key prompt.")
    admin("login", provider)
    console.print(f"Connected to {provider}.")


def print_rows(rows, args):
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], indent=2))
        return
    table = Table(box=None, header_style="dim", highlight=False)
    if args.rented:
        for name in ("Name", "GPU", "Status", "USD / hr", "Region", "ID"):
            table.add_column(name)
        for row in rows:
            table.add_row(row.name, row.gpu, row.status, f"${row.hourly:.3f}", row.region, row.id)
    else:
        for name in ("GPU", "VRAM", "USD / hr", "Region", "Offer"):
            table.add_column(name)
        for row in sorted_offers(rows, args.sort, args.disk):
            total = row.total(args.disk)
            price = f"${total:.3f}" if total is not None else f"${row.hourly:.3f}+"
            table.add_row(row.title, f"{row.vram:g} GB", price, row.region, row.id)
    console.print(table)


def resolve(api, target: str):
    matches = [row for row in api.instances() if row.id == target or row.name == target]
    if len(matches) != 1:
        raise CrigError("Choose one exact instance name or ID from crig --rented.")
    return matches[0]


def rent(api, rental: Rental, args):
    if args.demo:
        console.print("Demo only. No GPU was rented.")
        return
    identifier = api.rent(rental)
    state.remember_instance(api.name, identifier)
    console.print(Text(f"Rented {rental.name} · {identifier}", style="green"))
    return identifier


def action(api, item, name: str, args):
    if name not in {"code", "stop", "destroy"}:
        raise CrigError(
            "Choose code, stop, or destroy. Other workflows are deferred; see docs/milestones.md."
        )
    if args.demo:
        console.print(f"Demo: {name} {item.name}. No changes made.")
        return
    if name == "code":
        with waiting() as progress:
            connect_editor(api, item.id, progress=progress)
        return
    if name == "destroy":
        prompt_confirm(f"Delete {item.name} and its instance-local files?")
    getattr(api, name)(item.id)
    console.print(f"{'Stop' if name == 'stop' else 'Delete'} request accepted for {item.name}.")


def dashboard(api, args):
    error = None
    selected_id = None
    while True:
        result = CloudrigApp(
            api,
            query=args.gpu or "",
            rented=args.rented,
            disk=args.disk,
            count=args.count,
            sort=args.sort,
            demo=args.demo,
            action_error=error,
            selected_id=selected_id,
        ).run()
        if result is None:
            return
        error = None
        try:
            if isinstance(result, Rental):
                selected_id = rent(api, result, args)
            elif isinstance(result, InstanceAction):
                action(api, result.instance, result.action, args)
                selected_id = result.instance.id
        except CrigError as failure:
            error = str(failure)
        args.rented = True


def doctor():
    provider = state.load().get("provider") or "vastai"
    check_service(provider)
    console.print(
        "Protected service is ready. Editor connections need VS Code with Remote - SSH and OpenSSH 8.9+."
    )


def dispatch(args):
    if args.disk < 1 or args.count < 1:
        raise CrigError("Disk size and GPU count must be positive.")
    provider = args.provider or ("vastai" if args.demo else state.load().get("provider"))
    if not provider:
        raise CrigError("Run crig in your terminal to connect an account, or use crig --demo.")
    api = Demo() if args.demo else BrokerProvider(provider)
    if args.words:
        if args.words == ["login"]:
            if args.demo:
                raise CrigError("Demo mode does not change accounts.")
            login(provider)
            return
        if len(args.words) != 2:
            raise CrigError("Use crig NAME code, stop, or destroy. Run crig for the dashboard.")
        name, command = args.words
        if command not in {"code", "stop", "destroy"}:
            raise CrigError("Only code, stop, and destroy are available. Other workflows are deferred.")
        action(api, resolve(api, name), command, args)
        return
    args.rented = args.gpu is None
    if args.plain or args.json or not sys.stdin.isatty():
        rows = api.instances() if args.rented else api.offers(args.gpu or "", args.disk, args.count)
        print_rows(rows, args)
    else:
        dashboard(api, args)


def main():
    argv = sys.argv[1:]
    try:
        if argv and argv[0] == "setup":
            from cloudrig.onboarding import setup

            setup(argv[1:])
            return
        if argv and argv[0] in {"update", "uninstall"}:
            from cloudrig.installation import uninstall, update

            (update if argv[0] == "update" else uninstall)(argv[1:])
            return
        if argv == ["doctor"]:
            doctor()
            return
        if argv == ["self-test"]:
            admin("self-test")
            console.print("Protected service checks passed.")
            return
        if not argv and sys.stdin.isatty():
            if not state.load().get("provider"):
                from cloudrig.onboarding import setup

                if not setup([]):
                    return
            else:
                from cloudrig.installation import ensure_service

                ensure_service(state.load()["provider"])
        dispatch(parser().parse_intermixed_args(argv))
    except CrigError as error:
        console.print(Text(f"crig: {error}", style="#edb19e"))
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        console.print("\nCancelled. Check crig --rented if a rental was submitted.")
        raise SystemExit(130) from None
    except OSError as error:
        console.print(Text(f"crig: {error.strerror or 'Local operation failed'}", style="#edb19e"))
        raise SystemExit(1) from None

    except Exception as error:
        console.print("Unexpected failure. No automatic retry was made. Check crig --rented before retrying.")
        console.print(f"Diagnostic: {json.dumps(safe_diagnostic(error))}", markup=False)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
