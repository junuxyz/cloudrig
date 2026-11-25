"""Terminal progress and quiet command execution."""

import subprocess
from contextlib import contextmanager

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from cloudrig.state import child_environment
from cloudrig.style import CLI_THEME

console = Console(highlight=False, theme=CLI_THEME)


def run_quietly(command: list[str], *, cwd=None):
    """Show noninteractive tool output only when the tool fails."""
    result = subprocess.run(
        command,
        cwd=cwd,
        env=child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode and result.stdout:
        console.print(Text(result.stdout.rstrip()))
    return result


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
