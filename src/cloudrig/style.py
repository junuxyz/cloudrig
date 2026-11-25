"""Visual tokens, glyphs, themes, and display formats shared by the TUI, CLI, and /dev/tty."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from rich.theme import Theme as RichTheme
from textual.app import App
from textual.color import Color
from textual.theme import Theme

PROVIDER_LABELS = {"vastai": "Vast.ai"}

# Canonical glyph vocabulary (all UI glyphs live here).
STATUS_RUNNING = "●"
STATUS_STOPPED = "○"
STATUS_ERROR = "✕"
SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SPINNER_INTERVAL = 0.08
SORT_ASC = "↑"
SORT_DESC = "↓"
FILTER_PREFIX = "/"
DISCLOSURE = "›"
RULE = "─"


@dataclass(frozen=True)
class ThemeTokens:
    canvas: str
    surface: str
    elevated: str
    hover: str
    active: str
    border_subtle: str
    border: str
    ink: str
    ink_2: str
    ink_3: str
    accent: str
    success: str
    warning: str
    error: str
    scrim: str


THEME_TOKENS: dict[str, ThemeTokens] = {
    "cloudrig": ThemeTokens(
        canvas="#0c0c0d",
        surface="#131315",
        elevated="#18181b",
        hover="#1c1c20",
        active="#222227",
        border_subtle="#202024",
        border="#2e2e34",
        ink="#ededef",
        ink_2="#98989f",
        ink_3="#696970",
        accent="#89b4fa",
        success="#73c991",
        warning="#e2b368",
        error="#e57b87",
        scrim="#0c0c0dcc",
    ),
    "cloudrig-light": ThemeTokens(
        canvas="#fafafa",
        surface="#f3f3f4",
        elevated="#ffffff",
        hover="#ebebed",
        active="#e2e2e6",
        border_subtle="#e4e4e7",
        border="#d4d4d8",
        ink="#161618",
        ink_2="#525258",
        ink_3="#6e6e75",
        accent="#2563eb",
        success="#15803d",
        warning="#a16207",
        error="#c81e1e",
        scrim="#16161826",
    ),
    "cloudrig-warm": ThemeTokens(
        canvas="#0f0e0d",
        surface="#161513",
        elevated="#1d1b19",
        hover="#24221f",
        active="#2b2824",
        border_subtle="#262320",
        border="#36322d",
        ink="#edeae4",
        ink_2="#a39d94",
        ink_3="#767067",
        accent="#dfa87c",
        success="#7ec699",
        warning="#dfc074",
        error="#df7b7b",
        scrim="#0f0e0dcc",
    ),
}


def _build_theme(name: str, tokens: ThemeTokens, *, dark: bool) -> Theme:
    selection_alpha = "38" if dark else "26"
    return Theme(
        name=name,
        primary=tokens.accent,
        secondary=tokens.accent,
        accent=tokens.accent,
        warning=tokens.warning,
        error=tokens.error,
        success=tokens.success,
        foreground=tokens.ink,
        background=tokens.canvas,
        surface=tokens.surface,
        panel=tokens.elevated,
        dark=dark,
        variables={
            "input-selection-background": f"{tokens.accent}{selection_alpha}",
            "scrollbar": tokens.border,
            "scrollbar-hover": tokens.ink_3,
            "scrollbar-active": tokens.ink_2,
            "scrollbar-background": tokens.canvas,
            "scrollbar-background-hover": tokens.canvas,
            "scrollbar-background-active": tokens.canvas,
        },
    )


THEMES = (
    _build_theme("cloudrig", THEME_TOKENS["cloudrig"], dark=True),
    _build_theme("cloudrig-light", THEME_TOKENS["cloudrig-light"], dark=False),
    _build_theme("cloudrig-warm", THEME_TOKENS["cloudrig-warm"], dark=True),
    Theme(
        name="cloudrig-ansi",
        primary="ansi_blue",
        secondary="ansi_blue",
        accent="ansi_blue",
        warning="ansi_yellow",
        error="ansi_red",
        success="ansi_green",
        foreground="ansi_default",
        background="ansi_default",
        surface="ansi_default",
        panel="ansi_default",
        dark=True,
        variables={
            "input-selection-background": "ansi_blue",
            "scrollbar": "ansi_bright_black",
            "scrollbar-hover": "ansi_default",
            "scrollbar-active": "ansi_blue",
            "scrollbar-background": "ansi_default",
            "scrollbar-background-hover": "ansi_default",
            "scrollbar-background-active": "ansi_default",
        },
    ),
)

# The CLI cannot know the terminal background, so it maps to the terminal's own ANSI colors.
CLI_THEME = RichTheme(
    {
        "crig.title": "bold",
        "crig.muted": "dim",
        "crig.accent": "blue",
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


def _theme_values(theme: Theme) -> dict[str, str]:
    cached = _THEME_COLORS.get(theme.name)
    if cached is None or cached[0] is not theme:
        cached = _THEME_COLORS[theme.name] = (theme, theme.to_color_system().generate())
    return cached[1]


def _is_ansi_theme(theme: Theme, values: dict[str, str]) -> bool:
    if theme.name == "cloudrig-ansi":
        return True
    bg = Color.parse(values["background"])
    fg = Color.parse(values["foreground"])
    return bg.ansi is not None or fg.ansi is not None


def _derive_tokens(theme: Theme, values: dict[str, str]) -> ThemeTokens:
    """Derive a complete surface & ink ladder for any third-party/built-in Textual theme."""
    bg = Color.parse(values["background"])
    fg = Color.parse(values["foreground"])
    surface = Color.parse(values.get("surface", values["background"]))
    if surface == bg:
        surface = bg.blend(fg, 0.04 if theme.dark else 0.03)
    elevated = bg.blend(fg, 0.07 if theme.dark else 0.0)
    hover = bg.blend(fg, 0.09 if theme.dark else 0.06)
    active = bg.blend(fg, 0.13 if theme.dark else 0.10)
    border_subtle = bg.blend(fg, 0.10 if theme.dark else 0.12)
    border = bg.blend(fg, 0.18 if theme.dark else 0.20)
    ink_2 = bg.blend(fg, 0.62)
    ink_3 = bg.blend(fg, 0.42 if theme.dark else 0.52)
    return ThemeTokens(
        canvas=bg.hex,
        surface=surface.hex,
        elevated=elevated.hex,
        hover=hover.hex,
        active=active.hex,
        border_subtle=border_subtle.hex,
        border=border.hex,
        ink=fg.hex,
        ink_2=ink_2.hex,
        ink_3=ink_3.hex,
        accent=Color.parse(values["primary"]).hex,
        success=Color.parse(values["success"]).hex,
        warning=Color.parse(values["warning"]).hex,
        error=Color.parse(values["error"]).hex,
        scrim=bg.with_alpha(0.8).hex,
    )


def palette(app: App) -> dict[str, str]:
    """Return semantic theme colors for Rich text rendering."""
    theme = app.current_theme
    tokens = THEME_TOKENS.get(theme.name)
    if tokens is not None:
        return {
            "bg": tokens.canvas,
            "surface": tokens.surface,
            "elevated": tokens.elevated,
            "ink": tokens.ink,
            "ink-2": tokens.ink_2,
            "ink-3": tokens.ink_3,
            "border-subtle": tokens.border_subtle,
            "accent": tokens.accent,
            "success": tokens.success,
            "warning": tokens.warning,
            "error": tokens.error,
        }
    values = _theme_values(theme)
    if _is_ansi_theme(theme, values):
        return {
            "bg": "default",
            "surface": "default",
            "elevated": "default",
            "ink": "default",
            "ink-2": "bright_black",
            "ink-3": "bright_black",
            "border-subtle": "bright_black",
            "accent": "blue",
            "success": "green",
            "warning": "yellow",
            "error": "red",
        }
    derived = _derive_tokens(theme, values)
    return {
        "bg": derived.canvas,
        "surface": derived.surface,
        "elevated": derived.elevated,
        "ink": derived.ink,
        "ink-2": derived.ink_2,
        "ink-3": derived.ink_3,
        "border-subtle": derived.border_subtle,
        "accent": derived.accent,
        "success": derived.success,
        "warning": derived.warning,
        "error": derived.error,
    }


def css_variables(theme: Theme, variables: dict[str, str]) -> dict[str, str]:
    """Return surface, border, ink, and scrim CSS variables for this theme."""
    tokens = THEME_TOKENS.get(theme.name)
    if tokens is not None:
        return {
            "canvas": tokens.canvas,
            "surface": tokens.surface,
            "elevated": tokens.elevated,
            "hover": tokens.hover,
            "active": tokens.active,
            "border-subtle": tokens.border_subtle,
            "border": tokens.border,
            "ink": tokens.ink,
            "ink-2": tokens.ink_2,
            "ink-3": tokens.ink_3,
            "scrim": tokens.scrim,
        }
    if _is_ansi_theme(theme, variables):
        return {
            "canvas": "ansi_default",
            "surface": "ansi_default",
            "elevated": "ansi_default",
            "hover": "ansi_bright_black",
            "active": "ansi_bright_black",
            "border-subtle": "ansi_bright_black",
            "border": "ansi_bright_black",
            "ink": "ansi_default",
            "ink-2": "ansi_bright_black",
            "ink-3": "ansi_bright_black",
            "scrim": "ansi_default",
        }
    derived = _derive_tokens(theme, variables)
    return {
        "canvas": derived.canvas,
        "surface": derived.surface,
        "elevated": derived.elevated,
        "hover": derived.hover,
        "active": derived.active,
        "border-subtle": derived.border_subtle,
        "border": derived.border,
        "ink": derived.ink,
        "ink-2": derived.ink_2,
        "ink-3": derived.ink_3,
        "scrim": derived.scrim,
    }


def is_transitional(status: str) -> bool:
    return status not in {"running", "stopped", "exited", "failed", "error", "terminated"}


def status_style(status: str, colors: dict[str, str], *, frame: int = 0) -> tuple[str, str]:
    if status == "running":
        return STATUS_RUNNING, colors["success"]
    if status in {"stopped", "exited"}:
        return STATUS_STOPPED, colors["ink-3"]
    if status in {"failed", "error", "terminated"}:
        return STATUS_ERROR, colors["error"]
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


def total_vram(vram_per_gpu: float, count: int) -> str:
    """Total VRAM across all GPUs in an offer or instance."""
    return gb(vram_per_gpu * count)


def pct(share: float) -> str:
    return f"{share:.1%}"


def gpu_label(gpu: str, count: int) -> str:
    return f"{gpu} ×{count}" if count > 1 else gpu


def named(name: str, identifier: str) -> str:
    return f"{name} ({identifier})"


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS[provider]


def _runway_duration(hours: float) -> str:
    if hours <= 0:
        return "0m"
    total_minutes = int(hours * 60)
    if total_minutes < 1:
        return "<1m"
    if total_minutes < 60:
        return f"~{total_minutes}m"
    if hours < 48:
        h, m = divmod(total_minutes, 60)
        return f"~{h}h {m}m" if m else f"~{h}h"
    if hours < 24 * 60:
        days, rem_h = divmod(int(hours), 24)
        return f"~{days}d {rem_h}h" if rem_h else f"~{days}d"
    return ">60d"


def runway_compact(hours: float) -> str:
    """Compact runway duration for the top header (e.g. '~11h 38m', '0m', '>60d')."""
    return _runway_duration(hours)


def runway(hours: float, *, now: datetime | None = None, include_eta: bool = True) -> str:
    """Format remaining runway before balance reaches $0, optionally with local ETA."""
    duration = _runway_duration(hours)
    if hours <= 0 or not include_eta or hours >= 24 * 60:
        return f"{duration} until $0"

    current = now or datetime.now()
    eta = current + timedelta(hours=hours)
    when = f"{eta.strftime('%b')} {eta.day}, {eta.strftime('%H:%M')}"
    return f"{duration} ({when}) until $0"


def tty_card(title: str, rows: list[tuple[str, str]], *, note: str = "", tone: str = "default") -> str:
    """Format a quiet confirmation block for /dev/tty PIN prompts, respecting NO_COLOR."""
    use_color = not os.environ.get("NO_COLOR")
    reset = "\033[0m" if use_color else ""
    bold = "\033[1m" if use_color else ""
    dim = "\033[2m" if use_color else ""
    tone_code = {"warn": "\033[33m", "danger": "\033[31m"}.get(tone, "") if use_color else ""

    rule_line = RULE * 56
    lines = [
        f"  {bold}{title}{reset}",
        f"  {dim}{rule_line}{reset}",
    ]
    for label, value in rows:
        lines.append(f"  {dim}{label:<10}{reset}{value}")
    if note:
        lines.append(f"  {tone_code}{note}{reset}")
    return "\n".join(lines)
