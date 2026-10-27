"""Shared terminal output and local confirmations."""

import sys

from rich.console import Console
from rich.prompt import Confirm
from rich.text import Text

from cloudrig.models import CrigError

console = Console(highlight=False)


def prompt_confirm(message: str, yes: bool = False):
    if yes:
        return
    if not sys.stdin.isatty():
        raise CrigError("Confirmation requires a terminal. Use --yes only for an intended operation.")
    if not Confirm.ask(Text(message), default=False):
        raise CrigError("Cancelled. No operation was submitted.")
