import pytest
from textual.widgets import DataTable, Input, Static

from cloudrig.demo import Demo
from cloudrig.models import InstanceAction, Rental
from cloudrig.tui import Actions, Checkout, CloudrigApp, Help


@pytest.mark.parametrize("size", [(120, 36), (80, 24)])
async def test_search_sort_filter_and_cancel(size):
    api = Demo()
    app = CloudrigApp(api, "5090", demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert len(app.visible_rows) == 5
        assert app.visible_rows[0].id == "84021"
        await pilot.press("s")
        assert app.sort_mode == "vram"
        await pilot.press("/", "t", "o", "k", "y", "o", "enter")
        await pilot.pause()
        assert len(app.visible_rows) == 1 and app.visible_rows[0].region == "Tokyo, JP"
        assert app.query_one(DataTable).has_focus
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Checkout)
        await pilot.press("escape")
        assert not isinstance(app.screen, Checkout)
        assert app.return_value is None


async def test_checkout_validates_disk_then_returns_explicit_rental():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#rental-disk", Input).value = "99999"
        await pilot.click("#rent")
        assert isinstance(app.screen, Checkout)
        assert "at most" in str(app.screen.query_one("#checkout-error", Static).render())
        app.screen.query_one("#rental-disk", Input).value = "80"
        app.screen.query_one("#rental-name", Input).value = "my-training"
        await pilot.pause(0.4)
        await pilot.click("#rent")
        await pilot.pause()
    assert isinstance(app.return_value, Rental)
    assert app.return_value.disk == 80 and app.return_value.name == "my-training"


async def test_instances_actions_and_no_implicit_mutation():
    api = Demo()
    app = CloudrigApp(api, rented=True, demo=True)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert len(app.visible_rows) == 3
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Actions)
        await pilot.click("#do-code")
        await pilot.pause()
    assert isinstance(app.return_value, InstanceAction)
    assert app.return_value.action == "code"


async def test_empty_search_and_recovery():
    app = CloudrigApp(Demo(), "does-not-exist", demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.visible_rows
        await pilot.press("enter", "s", "r")
        await pilot.pause()
        assert not app.visible_rows
        assert len(app.screen_stack) == 1


async def test_custom_image_is_preserved_in_checkout():
    app = CloudrigApp(Demo(), "5090", demo=True, image="custom/image:cuda")
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#rental-image", Input).value == "custom/image:cuda"


async def test_tabs_switch_views_and_action_hotkeys():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert app.rented and len(app.visible_rows) == 3
        await pilot.press("j", "k", "enter")
        await pilot.pause()
        assert isinstance(app.screen, Actions)
        await pilot.press("c")
        await pilot.pause()
    assert isinstance(app.return_value, InstanceAction)
    assert app.return_value.action == "code"


async def test_escape_clears_filter_and_delete_has_no_hotkey():
    app = CloudrigApp(Demo(), rented=True, demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("/", "z", "z", "enter")
        await pilot.pause()
        assert not app.visible_rows
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.visible_rows) == 3
        await pilot.press("enter")
        await pilot.pause()
        for key in "wertyuiopasdfghjklzxcvbnm":
            if key not in Actions.HOTKEYS:
                await pilot.press(key)
        assert isinstance(app.screen, Actions)


async def test_theme_choice_is_remembered_outside_demo():
    app = CloudrigApp(Demo(), "5090")
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        app.theme = "cloudrig-light"
        await pilot.pause()
    again = CloudrigApp(Demo(), "5090")
    async with again.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert again.theme == "cloudrig-light"


async def test_splash_help_and_sorted_header():
    app = CloudrigApp(Demo(), "does-not-exist", demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        splash = app.query_one("#splash", Static)
        assert splash.display and "No offers" in str(splash.render())
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, Help)
        await pilot.press("escape")
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert not app.query_one("#splash", Static).display
        await pilot.press("down", "s")
        await pilot.pause()
        headers = [str(column.label) for column in app.query_one(DataTable).columns.values()]
        assert "VRAM ↓" in headers
        assert app.visible_rows[app.query_one(DataTable).cursor_row].id == app.selected_id
