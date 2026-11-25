from datetime import datetime

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
        (style.total_vram(32.0, 4), "128 GB"),
        (style.total_vram(24.0, 1), "24 GB"),
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


@pytest.mark.parametrize("name", list(style.THEME_TOKENS))
def test_every_text_ink_meets_contrast_across_surface_tiers(name):
    tokens = style.THEME_TOKENS[name]
    tiers = {"canvas": tokens.canvas, "surface": tokens.surface, "elevated": tokens.elevated}
    for tier_name, bg in tiers.items():
        assert contrast(tokens.ink_3, bg) >= 3.2, f"{name} ink-3 on {tier_name}"
        min_aa = 4.4 if tier_name == "surface" else 4.5
        for token_name in ("ink", "ink_2", "accent", "success", "warning", "error"):
            assert contrast(getattr(tokens, token_name), bg) >= min_aa, f"{name} {token_name} on {tier_name}"


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


@pytest.mark.parametrize("theme_name", ["cloudrig-warm", "cloudrig-ansi", "textual-light"])
async def test_dashboard_styles_curated_and_builtin_themes(theme_name):
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test() as pilot:
        app.theme = theme_name
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Checkout)


async def test_call_to_action_and_password_note_fit_on_one_line():
    app = CloudrigApp(Demo(), "5090", demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        cta = app.query_one("#inspector-cta")
        assert len(cta.render().plain.splitlines()) == 1
        assert len(cta.render().plain) <= cta.content_size.width
        await pilot.press("enter")
        await pilot.pause()
        note = app.screen.query_one("#next-step")
        assert len(str(note.render())) <= note.content_size.width


def test_plain_tables_use_dashboard_columns_and_total_vram(monkeypatch, capsys):
    from cloudrig import cli

    monkeypatch.setattr("sys.argv", ["crig", "--gpu", "5090", "--plain", "--demo"])
    cli.main()
    out = capsys.readouterr().out
    assert out.split()[:5] == ["gpu", "vram", "price/hr", "region", "offer"]
    assert "128 GB" in out

    monkeypatch.setattr("sys.argv", ["crig", "--rented", "--plain", "--demo"])
    cli.main()
    assert capsys.readouterr().out.split()[:6] == ["name", "status", "gpu", "price/hr", "region", "id"]


def test_runway_formats_duration_compact_and_zero_dollar_eta():
    anchor = datetime(2026, 11, 25, 14, 0)
    assert style.runway_compact(0) == "0m"
    assert style.runway_compact(0.5) == "~30m"
    assert style.runway_compact(12.5) == "~12h 30m"
    assert style.runway_compact(24 * 10) == "~10d"
    assert style.runway_compact(24 * 90) == ">60d"

    assert style.runway(0, now=anchor) == "0m until $0"
    assert style.runway(0.5, now=anchor) == "~30m (Nov 25, 14:30) until $0"
    assert style.runway(12.5, now=anchor) == "~12h 30m (Nov 26, 02:30) until $0"
    assert style.runway(12.5, now=anchor, include_eta=False) == "~12h 30m until $0"
    assert style.runway(24 * 10, now=anchor) == "~10d (Dec 5, 14:00) until $0"
    assert style.runway(24 * 90, now=anchor) == ">60d until $0"


def test_tty_card_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    card = style.tty_card("Rent train-1", [("gpu", "RTX 5090"), ("rate", "$0.426/hr")], note="Note.")
    assert "\033[" not in card
    assert "Rent train-1" in card
    assert "RTX 5090" in card
    assert "Note." in card
