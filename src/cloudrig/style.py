"""Visual tokens and display formats shared by the dashboard and the CLI."""

from datetime import datetime, timedelta

from rich.theme import Theme as RichTheme
from textual.app import App
from textual.color import Color
from textual.theme import Theme

PROVIDER_LABELS = {"vastai": "Vast.ai"}

# Text ink as a share of the foreground blended over the background. Light ink-3 is higher
# so it keeps 3:1 contrast on its paler background.
INK = {
    "cloudrig": {"ink-2": 0.62, "ink-3": 0.38},
    "cloudrig-light": {"ink-2": 0.62, "ink-3": 0.50},
}

# Fills and hairlines as foreground alpha, so they read the same on background and surface.
FILLS = {"tint-1": 0.04, "tint-2": 0.08, "ink-4": 0.10}

# Rich's "dots" spinner, so dashboard and CLI waits look the same.
SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SPINNER_INTERVAL = 0.08

THEMES = (
    Theme(
        name="cloudrig",
        primary="#a5b4fc",
        secondary="#a5b4fc",
        accent="#a5b4fc",
        warning="#fcd34d",
        error="#fca5a5",
        success="#86efac",
        foreground="#ededed",
        background="#0b0b0c",
        surface="#111113",
        panel="#18181b",
        dark=True,
        variables={
            "input-selection-background": "#a5b4fc40",
            "scrollbar": "#1f1f23",
            "scrollbar-hover": "#2a2a2f",
            "scrollbar-active": "#3a3a40",
            "scrollbar-background": "#0b0b0c",
            "scrollbar-background-hover": "#0b0b0c",
            "scrollbar-background-active": "#0b0b0c",
        },
    ),
    Theme(
        name="cloudrig-light",
        primary="#4f46e5",
        secondary="#4f46e5",
        accent="#4f46e5",
        warning="#a16207",
        error="#b91c1c",
        success="#15803d",
        foreground="#18181b",
        background="#fcfcfc",
        surface="#f6f6f7",
        panel="#efeff1",
        dark=False,
        variables={"input-selection-background": "#4f46e533"},
    ),
)

# The CLI cannot know the terminal background, so it maps to the terminal's own ANSI colors.
CLI_THEME = RichTheme(
    {
        "crig.title": "bold",
        "crig.muted": "dim",
        "crig.ok": "green",
        "crig.warn": "yellow",
        "crig.error": "red",
        "prompt.choices": "dim",
        "prompt.default": "dim",
        "prompt.invalid": "red",
        "prompt.invalid.choice": "red",
        "progress.spinner": "default",
        "progress.elapsed": "dim",
    }
)

_THEME_COLORS: dict[str, tuple[Theme, dict[str, str]]] = {}


def palette(app: App) -> dict[str, str]:
    """Return theme colors for Rich text."""
    # app.theme_variables can lag a theme change by one refresh.
    theme = app.current_theme
    cached = _THEME_COLORS.get(theme.name)
    if cached is None or cached[0] is not theme:
        cached = _THEME_COLORS[theme.name] = (theme, theme.to_color_system().generate())
    values = cached[1]
    ink = INK.get(theme.name, INK["cloudrig"])
    background, foreground = Color.parse(values["background"]), Color.parse(values["foreground"])
    return {
        "bg": background.hex,
        "ink": foreground.hex,
        "ink-2": background.blend(foreground, ink["ink-2"]).hex,
        "ink-3": background.blend(foreground, ink["ink-3"]).hex,
        "accent": values["primary"],
        "success": values["success"],
        "warning": values["warning"],
        "error": values["error"],
    }


def css_variables(theme: Theme, variables: dict[str, str]) -> dict[str, str]:
    """Return the ink, fill, and scrim variables the dashboard CSS uses for this theme."""
    ink = INK.get(theme.name, INK["cloudrig"])
    foreground = Color.parse(variables["foreground"])
    return {
        "ink": foreground.hex,
        "ink-2": foreground.with_alpha(ink["ink-2"]).hex,
        "ink-3": foreground.with_alpha(ink["ink-3"]).hex,
        **{name: foreground.with_alpha(alpha).hex for name, alpha in FILLS.items()},
        "scrim": Color.parse(variables["background"]).with_alpha(0.8).hex,
    }


def is_transitional(status: str) -> bool:
    return status not in {"running", "stopped", "exited", "failed", "error", "terminated"}


def status_style(status: str, colors: dict[str, str], *, frame: int = 0) -> tuple[str, str]:
    if status == "running":
        return "●", colors["success"]
    if status in {"stopped", "exited"}:
        return "○", colors["ink-3"]
    if status in {"failed", "error", "terminated"}:
        return "✕", colors["error"]
    return SPINNER[frame % len(SPINNER)], colors["warning"]


# Display formats. Each one is the only way its kind of value is written.


def usd(amount: float, *, digits: int = 3, partial: bool = False) -> str:
    """An amount without a period, for table cells and split headline prices."""
    return f"${amount:,.{digits}f}" + ("+" if partial else "")


def per_hour(amount: float, *, digits: int = 3, partial: bool = False) -> str:
    return f"{usd(amount, digits=digits, partial=partial)}/hr"


def per_day(amount: float) -> str:
    return f"{usd(amount, digits=2)}/day"


def per_month(amount: float) -> str:
    return f"{usd(amount, digits=0)}/mo"


def gb(size: float) -> str:
    return f"{size:g} GB"


def pct(share: float) -> str:
    return f"{share:.1%}"


def gpu_label(gpu: str, count: int) -> str:
    return f"{gpu} ×{count}" if count > 1 else gpu


def named(name: str, identifier: str) -> str:
    return f"{name} ({identifier})"


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS[provider]


def runway(hours: float, *, now: datetime | None = None, include_eta: bool = True) -> str:
    """Format remaining runway before balance reaches $0, optionally with local ETA."""
    if hours <= 0:
        return "0m until $0"
    total_minutes = int(hours * 60)
    if total_minutes < 1:
        duration = "<1m"
    elif total_minutes < 60:
        duration = f"~{total_minutes}m"
    elif hours < 48:
        h, m = divmod(total_minutes, 60)
        duration = f"~{h}h {m}m" if m else f"~{h}h"
    elif hours < 24 * 60:
        days, rem_h = divmod(int(hours), 24)
        duration = f"~{days}d {rem_h}h" if rem_h else f"~{days}d"
    else:
        duration = ">60d"

    if not include_eta or hours >= 24 * 60:
        return f"{duration} until $0"

    current = now or datetime.now()
    eta = current + timedelta(hours=hours)
    when = f"{eta.strftime('%b')} {eta.day}, {eta.strftime('%H:%M')}"
    return f"{duration} ({when}) until $0"
