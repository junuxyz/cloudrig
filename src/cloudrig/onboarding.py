"""Guide first-time users through protected installation and account setup."""

import argparse
import sys
from pathlib import Path

from rich.prompt import Prompt
from rich.text import Text

from cloudrig import state
from cloudrig.installation import ensure_service
from cloudrig.models import CrigError
from cloudrig.style import provider_label
from cloudrig.terminal import console


def setup(argv: list[str]) -> bool:
    parser = argparse.ArgumentParser(prog="crig setup", description="Connect a GPU account step by step.")
    parser.add_argument("--source", type=Path, help="Build the helper from source, for contributors")
    parser.add_argument(
        "--service-only", action="store_true", help="Update the service without replacing API keys"
    )
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        raise CrigError("Run crig setup in your own terminal. Agents should follow docs/agents.md.")
    if args.service_only:
        ensure_service(state.load().get("provider") or "vastai", args.source)
        console.print("Protected service is ready.", style="crig.ok")
        return True
    from cloudrig.cli import login

    console.print("\nCloudrig setup\n", style="crig.title")
    options = {"1": "vastai", "2": "runpod"}
    for key, label in [*((key, provider_label(value)) for key, value in options.items()), ("0", "Cancel")]:
        console.print(Text.assemble(key, ("  " + label, "crig.muted")))
    choice = Prompt.ask("Provider", choices=["0", *options], default="1")
    if choice == "0":
        return False
    provider = options[choice]
    ensure_service(provider, args.source)
    login(provider)
    state.update(provider=provider)
    console.print("\nSetup complete.")
    return True
