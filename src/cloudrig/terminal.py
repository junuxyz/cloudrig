"""Print progress and ask for confirmation in the local terminal."""

import sys
from contextlib import contextmanager

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.text import Text

from cloudrig.models import CrigError

console = Console(highlight=False)


@contextmanager
def waiting():
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not console.is_terminal,
    ) as progress:
        task = progress.add_task("Waiting for the GPU", total=None)
        yield lambda message: progress.update(task, description=message)


def prompt_confirm(message: str, yes: bool = False):
    if yes:
        return
    if not sys.stdin.isatty():
        raise CrigError("Confirmation requires a terminal. Use --yes only for an intended operation.")
    if not Confirm.ask(Text(message), default=False):
        raise CrigError("Cancelled. No operation was submitted.")
