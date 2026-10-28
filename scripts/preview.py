"""Render the demo terminal views without provider access or preference writes."""

import asyncio
import os
import time
from pathlib import Path

from cloudrig.demo import Demo
from cloudrig.project import Project, candidates
from cloudrig.tui import CloudrigApp
from cloudrig.workspace_tui import WorkspaceApp


async def main():
    os.environ.pop("NO_COLOR", None)
    root = Path(__file__).resolve().parents[1]
    project = Project(Path("/home/user/finetune"), "finetune", vram=32, image=Demo.default_image)
    offers = candidates(project, Demo().offers(""))
    began = time.time() - 75
    preparing = CloudrigApp(Demo(), rented=True, demo=True)
    preparing.preparations = {
        "demo-01": {
            "name": "training",
            "phase": "preparing",
            "step": "Uploading the project",
            "started": began,
            "history": [
                {"step": "Waiting for the GPU", "at": began},
                {"step": "Connecting over SSH", "at": began + 12},
                {"step": "Running startup script", "at": began + 30},
                {"step": "Uploading the project", "at": began + 60},
            ],
        }
    }
    details = CloudrigApp(Demo(), rented=True, demo=True)
    details.preparations = preparing.preparations.copy()
    previews = [
        (details, (120, 36), "setup-details.svg", "g"),
        (preparing, (120, 36), "preparing.svg", None),
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "market.svg", None),
        (CloudrigApp(Demo(), "5090", demo=True), (80, 24), "market-compact.svg", None),
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "checkout.svg", "enter"),
        (CloudrigApp(Demo(), rented=True, demo=True), (120, 36), "workspaces.svg", None),
        (WorkspaceApp(project, offers, demo=True), (120, 36), "project-workspace.svg", None),
        (WorkspaceApp(project, offers, demo=True), (80, 32), "project-workspace-compact.svg", None),
    ]
    for app, size, filename, action in previews:
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            if action:
                await pilot.press(action)
                await pilot.pause()
            svg = app.export_screenshot(title="Cloudrig / demo")
            (root / "docs" / filename).write_text(
                "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
