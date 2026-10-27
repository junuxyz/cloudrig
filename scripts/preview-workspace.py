"""Capture the real demo TUI at desktop and compact sizes, without provider access."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

from cloudrig.demo import Demo
from cloudrig.project import candidates, load_project
from cloudrig.workspace_tui import WorkspaceApp


def capture(app, path, title):
    svg = app.export_screenshot(title=title)
    path.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n")


async def main():
    os.environ.pop("NO_COLOR", None)
    root = Path(__file__).resolve().parents[1]
    project = replace(load_project(str(root), vram=32), image=Demo.default_image)
    offers = candidates(project, Demo().offers(""))
    for width, height, filename in [
        (120, 36, "project-workspace.svg"),
        (80, 32, "project-workspace-compact.svg"),
    ]:
        app = WorkspaceApp(project, offers, demo=True)
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            capture(app, root / "docs" / filename, "Cloudrig project workspace · demo")


if __name__ == "__main__":
    asyncio.run(main())
