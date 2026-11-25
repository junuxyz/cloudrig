"""Render the core dashboard with fictional data."""

import asyncio
from pathlib import Path

from cloudrig.demo import Demo
from cloudrig.tui import CloudrigApp


async def main():
    root = Path(__file__).resolve().parents[1] / "assets"
    previews = [
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "market.svg", None, "cloudrig"),
        (CloudrigApp(Demo(), "5090", demo=True), (80, 24), "market-compact.svg", None, "cloudrig"),
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "checkout.svg", "enter", "cloudrig"),
        (CloudrigApp(Demo(), "5090", demo=True), (120, 36), "checkout-light.svg", "enter", "cloudrig-light"),
        (CloudrigApp(Demo(), rented=True, demo=True), (120, 36), "instances.svg", None, "cloudrig"),
        (CloudrigApp(Demo(), rented=True, demo=True), (120, 36), "instance-actions.svg", "enter", "cloudrig"),
    ]
    for app, size, filename, action, theme in previews:
        async with app.run_test(size=size) as pilot:
            app.theme = theme
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            if action:
                await pilot.press(action)
                await pilot.pause()
            svg = app.export_screenshot(title="Cloudrig / demo")
            (root / filename).write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
