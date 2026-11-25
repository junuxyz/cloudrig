import pytest
from textual.color import Color

from cloudrig import style
from cloudrig.demo import Demo
from cloudrig.tui import Checkout, CloudrigApp


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (style.per_hour(0.426), "$0.426/hr"),
        (style.per_hour(0.42, partial=True), "$0.420+/hr"),
        (style.per_hour(0.013312, digits=4), "$0.0133/hr"),
        (style.per_day(10.2144), "$10.21/day"),
        (style.per_month(1306.4), "$1,306/mo"),
        (style.usd(1.5), "$1.500"),
        (style.gb(32.0), "32 GB"),
        (style.gb(0.5), "0.5 GB"),
        (style.pct(0.998), "99.8%"),
        (style.gpu_label("RTX 5090", 2), "RTX 5090 ×2"),
        (style.gpu_label("A100 SXM4", 1), "A100 SXM4"),
        (style.named("inference", "demo-02"), "inference (demo-02)"),
        (style.provider_label("vastai"), "Vast.ai"),
    ],
)
def test_display_formats(text, expected):
    assert text == expected


def contrast(a: str, b: str) -> float:
    def luminance(hex_color):
        r, g, b = (
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in (channel / 255 for channel in Color.parse(hex_color).rgb)
        )
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", [theme.name for theme in style.THEMES])
async def test_every_text_ink_is_readable(theme):
    app = CloudrigApp(Demo(), demo=True)
    async with app.run_test() as pilot:
        app.theme = theme
        await pilot.pause()
        colors = style.palette(app)
        for ink in ("ink", "ink-2", "ink-3", "accent", "success", "warning", "error"):
            assert contrast(colors[ink], colors["bg"]) >= 3, ink


async def test_open_dialog_follows_a_theme_change():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        app.theme = "cloudrig-light"
        await pilot.pause()
        quote = app.screen.query_one("#quote").render()
        assert quote.spans[0].style.foreground.hex == style.palette(app)["ink"]


async def test_dashboard_styles_any_builtin_theme():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test() as pilot:
        app.theme = "textual-light"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Checkout)
        assert app.screen.query_one(".esc").styles.color.a < 1


async def test_call_to_action_and_password_note_fit_on_one_line():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        inspector = app.query_one("#inspector")
        assert len(inspector.render().plain.splitlines()[-1]) <= inspector.content_size.width
        await pilot.press("enter")
        await pilot.pause()
        note = app.screen.query_one("#next-step")
        assert len(str(note.render())) <= note.content_size.width


def test_plain_tables_use_dashboard_columns(monkeypatch, capsys):
    from cloudrig import cli

    monkeypatch.setattr("sys.argv", ["crig", "--gpu", "5090", "--plain", "--demo"])
    cli.main()
    assert capsys.readouterr().out.split()[:5] == ["gpu", "vram", "price/hr", "region", "offer"]
    monkeypatch.setattr("sys.argv", ["crig", "--rented", "--plain", "--demo"])
    cli.main()
    assert capsys.readouterr().out.split()[:6] == ["name", "status", "gpu", "price/hr", "region", "id"]
