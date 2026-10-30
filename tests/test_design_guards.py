"""Keep the design system in docs/design-system.md from drifting."""

import re
from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from cloudrig.demo import Demo
from cloudrig.tui import CloudrigApp

SOURCES = sorted((Path(__file__).parents[1] / "src/cloudrig").glob("*.py"))


@pytest.mark.parametrize("path", [path for path in SOURCES if path.name != "style.py"], ids=lambda p: p.name)
def test_colors_and_opacities_live_only_in_style(path):
    source = path.read_text()
    assert not re.findall(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b", source), "hex color outside style.py"
    assert not re.findall(r"\$[a-z-]+ \d+%", source), "CSS opacity outside style.py; use an ink or tint"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_middle_dot_separator(path):
    assert "·" not in path.read_text(), "separate inline items with three spaces"


def clipped(app) -> list[str]:
    """Describe every visible text widget whose content does not fit its box."""
    problems = []
    for widget in app.screen.query(Static):
        width, height = widget.content_size
        if not widget.region or not width:
            continue
        name = widget.id or " ".join(widget.classes) or type(widget).__name__
        needed = widget.get_content_height(app.screen.size, app.size, width)
        if needed > height:
            problems.append(f"{name}: needs {needed} rows, has {height}")
        # render() converts Rich Text and drops no_wrap; widgets that override render() return Text.
        visual = widget.content if isinstance(widget.content, Text) else widget.render()
        if isinstance(visual, Text) and visual.no_wrap:
            for line in visual.plain.splitlines():
                if cell_len(line) > width:
                    problems.append(f"{name}: {line!r} is wider than {width}")
    return problems


@pytest.mark.parametrize(
    ("query", "rented", "keys"),
    [
        ("5090", False, []),
        ("5090", False, ["slash"]),
        ("5090", False, ["enter"]),
        ("5090", False, ["question_mark"]),
        ("zzz", False, []),
        ("", True, []),
        ("", True, ["enter"]),
    ],
    ids=["offers", "filter", "checkout", "help", "empty", "instances", "actions"],
)
async def test_nothing_is_clipped_at_80_by_24(query, rented, keys):
    app = CloudrigApp(Demo(), query, rented=rented, demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        assert clipped(app) == []
