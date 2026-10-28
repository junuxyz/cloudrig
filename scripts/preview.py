"""Render the core dashboard with fictional data."""

import asyncio
from pathlib import Path

from cloudrig.demo import Demo
from cloudrig.tui import CloudrigApp


async def main():
    root = Path(__file__).resolve().parents[1] / "docs"
    previews = [
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "market.svg", None),
        (CloudrigApp(Demo(), "5090", demo=True), (80, 24), "market-compact.svg", None),
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "checkout.svg", "enter"),
        (CloudrigApp(Demo(), rented=True, demo=True), (120, 36), "instances.svg", None),
        (CloudrigApp(Demo(), rented=True, demo=True), (120, 36), "instance-actions.svg", "enter"),
    ]
    for app, size, filename, action in previews:
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            if action:
                await pilot.press(action)
                await pilot.pause()
            svg = app.export_screenshot(title="Cloudrig / demo")
            (root / filename).write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
