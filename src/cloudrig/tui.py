import asyncio
import re
import time

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, DataTable, Input, Label, Static

from cloudrig import __version__, state
from cloudrig.models import CrigError, Instance, InstanceAction, Offer, Rental

ACCENT = "#fab283"
SORTS = ("price", "vram", "region", "gpus")
SORT_LABELS = {"price": "price ↑", "vram": "VRAM ↓", "region": "region A–Z", "gpus": "GPUs ↓"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SIDEBAR_MIN_WIDTH = 104
SORT_COLUMNS = {
    "price": ("USD / HR", "↑"),
    "vram": ("VRAM", "↓"),
    "region": ("REGION", "↑"),
    "gpus": ("GPU", "↓"),
}
# Five-row block letters for the splash wordmark: "cloud" is drawn muted and "rig" bright.
LOGO_FONT = {
    "c": ("    ", "█▀▀▀", "█   ", "▀▀▀▀", "    "),
    "l": ("█", "█", "█", "▀", " "),
    "o": ("    ", "█▀▀█", "█  █", "▀▀▀▀", "    "),
    "u": ("    ", "█  █", "█  █", "▀▀▀▀", "    "),
    "d": ("   █", "█▀▀█", "█  █", "▀▀▀▀", "    "),
    "r": ("    ", "█▀▀▀", "█   ", "▀   ", "    "),
    "i": ("▀", "█", "█", "▀", " "),
    "g": ("    ", "█▀▀█", "█  █", "▀▀▀█", "▀▀▀▀"),
}

THEMES = (
    Theme(
        name="cloudrig",
        primary=ACCENT,
        secondary="#5c9cf5",
        accent="#9d7cd8",
        warning="#f5a742",
        error="#e06c75",
        success="#7fd88f",
        foreground="#eeeeee",
        background="#0a0a0a",
        surface="#141414",
        panel="#1e1e1e",
        dark=True,
        variables={
            "input-selection-background": "#fab28340",
            "scrollbar": "#2a2a2a",
            "scrollbar-hover": "#3a3a3a",
            "scrollbar-active": "#4a4a4a",
            "scrollbar-background": "#0a0a0a",
            "scrollbar-background-hover": "#0a0a0a",
            "scrollbar-background-active": "#0a0a0a",
        },
    ),
    Theme(
        name="cloudrig-mint",
        primary="#a7efb5",
        secondary="#8ccfd8",
        accent="#f2c48d",
        warning="#f2c48d",
        error="#f0a191",
        success="#a7efb5",
        foreground="#e3eae5",
        background="#0b0f0e",
        surface="#121816",
        panel="#1a221f",
        dark=True,
        variables={
            "input-selection-background": "#a7efb540",
            "scrollbar": "#26302c",
            "scrollbar-hover": "#34413b",
            "scrollbar-active": "#3f4e47",
            "scrollbar-background": "#0b0f0e",
            "scrollbar-background-hover": "#0b0f0e",
            "scrollbar-background-active": "#0b0f0e",
        },
    ),
    Theme(
        name="cloudrig-light",
        primary="#c8612a",
        secondary="#3b6fd6",
        accent="#7c4fc4",
        warning="#b7791f",
        error="#c53b47",
        success="#2f8a3e",
        foreground="#1a1a1a",
        background="#fafafa",
        surface="#f1f1f1",
        panel="#e6e6e6",
        dark=False,
        variables={"input-selection-background": "#c8612a33"},
    ),
)


def sorted_offers(offers: list[Offer], sort: str, disk: int) -> list[Offer]:
    def price(row):
        total = row.total(disk)
        return total if total is not None else row.hourly

    keys = {
        "price": price,
        "vram": lambda row: -row.vram,
        "region": lambda row: row.region,
        "gpus": lambda row: -row.count,
    }
    return sorted(offers, key=lambda row: (keys[sort](row), price(row), row.id))


def rate(offer: Offer, disk: int) -> str:
    total = offer.total(disk)
    return f"${total:.3f}" if total is not None else f"${offer.hourly:.3f}+"


def hourly(offer: Offer, disk: int) -> float:
    total = offer.total(disk)
    return total if total is not None else offer.hourly


_THEME_COLORS: dict[str, tuple[Theme, dict[str, str]]] = {}


def theme_colors(theme: Theme) -> dict[str, str]:
    cached = _THEME_COLORS.get(theme.name)
    if cached is None or cached[0] is not theme:
        cached = _THEME_COLORS[theme.name] = (theme, theme.to_color_system().generate())
    return cached[1]


def palette(app: App) -> dict[str, str]:
    """Resolved theme colors for Rich text, which cannot read Textual CSS variables."""
    # Read the theme directly: app.theme_variables can lag a theme change by one refresh.
    values = theme_colors(app.current_theme)
    background, foreground = Color.parse(values["background"]), Color.parse(values["foreground"])
    return {
        "bg": background.hex,
        "fg": foreground.hex,
        "muted": foreground.blend(background, 0.42).hex,
        "faint": foreground.blend(background, 0.66).hex,
        "primary": values["primary"],
        "success": values["success"],
        "warning": values["warning"],
        "error": values["error"],
    }


def status_style(status: str, colors: dict[str, str]) -> tuple[str, str]:
    if status == "running":
        return "●", colors["success"]
    if status in {"stopped", "exited"}:
        return "○", colors["muted"]
    return "◐", colors["warning"]


def section(text: Text, title: str, width: int, colors: dict[str, str]) -> None:
    text.append(f"\n\n{title.upper()} ", style=f"bold {colors['muted']}")
    text.append("─" * max(width - len(title) - 1, 0), style=colors["faint"])
    text.append("\n")


def field(text: Text, label: str, value: str | Text, colors: dict[str, str]) -> None:
    text.append(f"\n{label:<13}", style=colors["muted"])
    if isinstance(value, Text):
        text.append_text(value)
    else:
        text.append(value, style=colors["fg"])


def logo(colors: dict[str, str]) -> Text:
    text = Text(justify="center")
    for line in range(5):
        text.append(" ".join(LOGO_FONT[char][line] for char in "cloud"), style=colors["muted"])
        text.append(" ")
        text.append(" ".join(LOGO_FONT[char][line] for char in "rig"), style=f"bold {colors['fg']}")
        text.append("\n")
    return text


class EscHint(Static):
    """Clickable 'esc' affordance in dialog headers."""

    def on_click(self) -> None:
        self.screen.dismiss(None)


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Back", show=False)]

    def header(self, title: str) -> ComposeResult:
        with Horizontal(classes="dialog-header"):
            yield Label(title, classes="dialog-title")
            yield EscHint("esc", id="back", classes="esc")

    def action_cancel(self):
        self.dismiss(None)


class Checkout(Dialog):
    def __init__(self, offer: Offer, image: str, disk: int):
        super().__init__()
        self.offer, self.image, self.disk = offer, image, disk

    def compose(self) -> ComposeResult:
        offer = self.offer
        with Vertical(id="checkout", classes="dialog"):
            yield from self.header(f"Rent {offer.title}")
            yield Static(id="checkout-title", classes="dialog-subtitle")
            yield Label("Workspace name", classes="field-label")
            yield Input(value=f"gpu-{offer.id.split('|')[-1].lower()}", id="rental-name")
            with Horizontal(classes="field-row"):
                with Vertical(classes="field-disk"):
                    yield Label("Disk · GB", classes="field-label")
                    yield Input(value=str(self.disk), type="integer", id="rental-disk")
                with Vertical(classes="field-image"):
                    yield Label("Container image", classes="field-label")
                    yield Input(value=self.image, id="rental-image")
            yield Static(id="quote")
            yield Static(id="checkout-note")
            yield Static("", id="checkout-error")
            with Horizontal(classes="dialog-actions"):
                yield Static(id="next-step")
                yield Button("Back", id="cancel")
                yield Button("Review & rent", variant="primary", id="rent")

    def on_mount(self):
        colors = palette(self.app)
        offer = self.offer
        subtitle = Text()
        subtitle.append(offer.region, style=colors["fg"])
        subtitle.append(f"  ·  {offer.provider}", style=colors["muted"])
        if offer.cloud:
            subtitle.append(f" {offer.cloud.lower()}", style=colors["muted"])
        subtitle.append(f"  ·  offer {offer.id}", style=colors["muted"])
        self.query_one("#checkout-title", Static).update(subtitle)
        note = Text()
        note.append("! ", style=f"bold {colors['warning']}")
        note.append(
            "Billing starts when rented. Stopping may retain storage charges. "
            "Deleting the instance removes its local files.",
            style=colors["muted"],
        )
        self.query_one("#checkout-note", Static).update(note)
        self.query_one("#next-step", Static).update(
            Text.assemble(("next  ", colors["faint"]), ("sudo → protected quote → rent", colors["muted"]))
        )
        self.update_quote()
        self.query_one("#rental-name", Input).focus()

    @on(Input.Changed, "#rental-disk")
    def disk_changed(self):
        self.update_quote()

    @on(Input.Submitted)
    def next_field(self, event: Input.Submitted):
        order = ["rental-name", "rental-disk", "rental-image"]
        current = event.input.id
        if current in order[:-1]:
            self.query_one(f"#{order[order.index(current) + 1]}", Input).focus()
        else:
            self.query_one("#rent", Button).focus()

    def update_quote(self):
        colors = palette(self.app)
        value = self.query_one("#rental-disk", Input).value
        disk = int(value) if value.isdigit() else 0
        offer = self.offer
        total = offer.total(disk)
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(style=colors["muted"], width=9)
        grid.add_column(justify="right", style=colors["fg"], width=11)
        grid.add_column(style=colors["muted"])
        grid.add_row("Compute", f"${offer.hourly:.3f}/hr", f"{offer.count} × {offer.gpu}")
        if total is None:
            grid.add_row("Storage", "extra", "rate is not in the GPU catalog")
            grid.add_row("", Text("─" * 11, style=colors["faint"]), "")
            grid.add_row(
                Text("Estimate", style=f"bold {colors['fg']}"),
                Text(f"${offer.hourly:.3f}+", style=f"bold {colors['primary']}"),
                "per hour, plus storage",
            )
        else:
            grid.add_row(
                "Storage", f"${total - offer.hourly:.3f}/hr", f"{disk} GB × ${offer.disk_hourly_gb:g}/GB"
            )
            grid.add_row("", Text("─" * 11, style=colors["faint"]), "")
            grid.add_row(
                Text("Estimate", style=f"bold {colors['fg']}"),
                Text(f"${total:.3f}/hr", style=f"bold {colors['primary']}"),
                Text.assemble(
                    (f"${total * 24:.2f}", colors["fg"]),
                    (" per 24 h  ·  ", colors["muted"]),
                    (f"${total * 24 * 30:,.0f}", colors["fg"]),
                    (" per 30 d", colors["muted"]),
                ),
            )
        self.query_one("#quote", Static).update(grid)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#rental-name", Input).value.strip()
        value = self.query_one("#rental-disk", Input).value
        image = self.query_one("#rental-image", Input).value.strip()
        error = ""
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}", name):
            error = "Use a name with letters, numbers, '-' or '_', up to 63 characters."
        elif not value.isdigit() or not 1 <= int(value) <= 100000:
            error = "Choose a disk size between 1 and 100000 GB."
        elif self.offer.disk_max is not None and int(value) > self.offer.disk_max:
            error = f"This offer has at most {self.offer.disk_max:g} GB disk."
        elif not image or any(c.isspace() for c in image):
            error = "Enter a container image without spaces."
        if error:
            colors = palette(self.app)
            message = Text.assemble(("✕ ", f"bold {colors['error']}"), (error, colors["error"]))
            self.query_one("#checkout-error", Static).update(message)
            return
        self.dismiss(Rental(self.offer, name, int(value), image))


class Choice(Static, can_focus=True):
    """One selectable row in a dialog list: label on the left, tag and hotkey on the right."""

    BINDINGS = [Binding("enter", "choose", show=False)]

    def __init__(self, label: str, value: str, key: str = "", tag: str = "", danger: bool = False):
        super().__init__(id=f"do-{value}", classes="choice -danger" if danger else "choice")
        self.label, self.value, self.key, self.tag = label, value, key, tag

    def render(self) -> Text:
        colors = palette(self.app)
        ink = colors["bg"] if self.has_focus else None
        text = Text(no_wrap=True, overflow="ellipsis")
        style = ink or (colors["error"] if self.has_class("-danger") else colors["fg"])
        text.append(f" {self.label}", style=f"bold {style}" if self.has_focus else style)
        right = Text()
        if self.tag:
            right.append(self.tag, style=ink or colors["warning"])
            right.append("   ")
        right.append(f"{self.key or ' '} ", style=f"bold {ink}" if ink else colors["muted"])
        text.append(" " * max(self.size.width - text.cell_len - right.cell_len, 1))
        text.append_text(right)
        return text

    def on_focus(self):
        self.refresh()

    def on_blur(self):
        self.refresh()

    def on_enter(self):
        self.focus()

    def on_click(self):
        self.action_choose()

    def action_choose(self):
        self.screen.dismiss(InstanceAction(self.screen.instance, self.value))


class Actions(Dialog):
    BINDINGS = [
        Binding("up,k", "app.focus_previous", show=False),
        Binding("down,j", "app.focus_next", show=False),
    ]
    # Delete deliberately has no single-key shortcut.
    HOTKEYS = {
        "p": "approve",
        "s": "ssh",
        "c": "code",
        "a": "agent",
        "u": "push",
        "d": "pull",
        "t": "start",
        "x": "stop",
    }

    def __init__(self, instance: Instance):
        super().__init__()
        self.instance = instance

    def groups(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        row = self.instance
        authorize = [("Reauthorize connection…", "approve", "sudo")]
        manage = []
        if row.status != "running":
            manage.append(("Start instance", "start", "sudo"))
        if row.status not in {"stopped", "exited"}:
            manage.append(("Stop instance", "stop", ""))
        if row.approved:
            manage += authorize
        manage.append(("Delete instance…", "destroy", "sudo"))
        authorization = "" if row.approved else "sudo"
        connect = [
            ("Connect with SSH", "ssh", authorization),
            ("Open in VS Code", "code", authorization),
            ("Run an agent", "agent", authorization),
        ]
        return [
            ("Connect", connect),
            ("Files", [("Send files to cloud", "push", ""), ("Get files locally", "pull", "")]),
            ("Manage", manage),
        ]

    def compose(self) -> ComposeResult:
        keys = {value: key for key, value in self.HOTKEYS.items()}
        with Vertical(id="instance-actions", classes="dialog"):
            yield from self.header(self.instance.name)
            yield Static(id="checkout-title", classes="dialog-subtitle")
            for title, options in self.groups():
                yield Label(title.upper(), classes="group-label")
                for label, value, tag in options:
                    yield Choice(label, value, keys.get(value, ""), tag, danger=value == "destroy")

    def on_mount(self):
        colors = palette(self.app)
        row = self.instance
        mark, color = status_style(row.status, colors)
        dot = ("  ·  ", colors["faint"])
        self.query_one("#checkout-title", Static).update(
            Text.assemble(
                (f"{mark} {row.status}", color),
                dot,
                (f"{row.gpu} ×{row.count}", colors["fg"]),
                dot,
                (row.region, colors["muted"]),
                dot,
                (f"${row.hourly:.3f}/hr", colors["fg"]),
            )
        )
        self.query(Choice).first().focus()

    def on_key(self, event):
        value = self.HOTKEYS.get(event.character or "")
        if value and self.query(f"#do-{value}"):
            event.stop()
            event.prevent_default()
            self.dismiss(InstanceAction(self.instance, value))


class Help(Dialog):
    KEYS = (
        (
            "Navigate",
            (
                ("↑ ↓  j k", "move the selection"),
                ("enter", "review an offer or open actions"),
                ("1  2", "market, workspaces"),
                ("esc", "back, or clear the filter"),
            ),
        ),
        ("Search", (("/", "filter the list"), ("s", "cycle sort order"), ("r", "refresh from the provider"))),
        (
            "Workspace actions",
            (
                ("s c a", "ssh, VS Code, agent"),
                ("u d", "send, get files"),
                ("p t x", "authorize, start, stop"),
            ),
        ),
        ("App", (("ctrl+p", "commands and themes"), ("?", "this help"), ("q", "quit"))),
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help", classes="dialog"):
            yield from self.header("Keyboard shortcuts")
            yield Static(id="help-body")

    def on_mount(self):
        colors = palette(self.app)
        text = Text()
        for index, (title, keys) in enumerate(self.KEYS):
            text.append("\n" if index else "", style=colors["fg"])
            text.append(f"{title.upper()}\n", style=f"bold {colors['muted']}")
            for key, label in keys:
                text.append(f"{key:<11}", style=f"bold {colors['fg']}")
                text.append(f"{label}\n", style=colors["muted"])
        text.append("\nDelete has no shortcut. Spending actions always ask for sudo.", style=colors["faint"])
        self.query_one("#help-body", Static).update(text)


class Tab(Static):
    def __init__(self, label: str, view: str):
        super().__init__(label, id=f"tab-{view}", classes="tab")
        self.view = view

    def on_click(self):
        self.app.action_view(self.view)


class CloudrigApp(App[Rental | InstanceAction | None]):
    TITLE = "cloudrig"
    SUB_TITLE = "GPU market"
    CSS = """
    Screen { background: $background; color: $foreground; }
    #shell { padding: 0 2; }

    #header { height: 3; padding: 1 0 0 0; }
    #brand { width: auto; padding-right: 2; }
    .tab { width: auto; padding: 0 2; color: $foreground 45%; }
    .tab:hover { color: $foreground; }
    .tab.-active { color: $foreground; text-style: bold; background: $panel; }
    #header-space { width: 1fr; }
    #provider { width: auto; }

    #searchbar { height: 1; margin: 1 0; }
    #search { width: 1fr; height: 1; border: none; padding: 0 1; background: $surface;
        border-left: outer $panel-lighten-2; }
    #search:focus { border-left: outer $primary; background: $panel; }
    #search > .input--placeholder { color: $foreground 35%; }
    #meta { width: auto; min-width: 20; height: 1; padding: 0 0 0 2; }

    #stage { height: 1fr; layers: base splash; }
    #body { height: 1fr; }
    #splash { layer: splash; width: 100%; height: 100%; background: $background;
        content-align: center middle; text-align: center; }
    #offers { width: 1fr; height: 1fr; min-height: 4; background: $background; scrollbar-size-vertical: 1;
        overflow-x: hidden; }
    Screen.-narrow #offers { overflow-x: auto; }
    #offers > .datatable--header { background: $background; color: $foreground 45%; text-style: bold; }
    #offers > .datatable--cursor { background: $primary 14%; text-style: bold; }
    #offers:blur > .datatable--cursor { background: $foreground 7%; text-style: none; }
    #offers > .datatable--hover { background: $foreground 5%; }
    #offers > .datatable--header-hover { background: $background; }
    #inspector { width: 44; height: 1fr; margin-left: 2; padding: 1 2; background: $surface; }
    Screen.-narrow #body { layout: vertical; }
    Screen.-narrow #inspector { width: 1fr; height: 5; margin: 1 0 0 0; padding: 1 1 0 1; }

    #statusbar { dock: bottom; height: 1; background: $surface; padding: 0 2; }
    #status-left { width: 1fr; }
    #status-right { width: auto; }

    Dialog { align: center middle; background: $background 70%; }
    .dialog { width: 80; height: auto; max-height: 95%; background: $surface; padding: 1 3;
        border-left: outer $primary; overflow-y: auto; scrollbar-size-vertical: 1; }
    #instance-actions { width: 64; }
    #help { width: 60; }
    #help-body { height: auto; margin-top: 1; }
    .dialog-header { height: 1; }
    .dialog-title { width: 1fr; text-style: bold; color: $foreground; }
    .esc { width: auto; color: $foreground 45%; }
    .esc:hover { color: $foreground; }
    .dialog-subtitle { height: auto; margin: 0 0 1 0; }
    .field-label { margin-top: 1; color: $foreground 55%; }
    .dialog Input { height: 1; border: none; padding: 0 1; background: $panel;
        border-left: outer $panel-lighten-2; }
    .dialog Input:focus { border-left: outer $primary; }
    .field-row { height: auto; }
    .field-disk { width: 18; height: auto; margin-right: 2; }
    .field-image { width: 1fr; height: auto; }
    #quote { height: auto; margin-top: 2; padding: 1 2; background: $panel; }
    #checkout-note { height: auto; margin-top: 1; }
    #checkout-error { height: auto; }
    .dialog-actions { height: 1; margin-top: 1; }
    #next-step { width: 1fr; height: 1; }
    Button { height: 1; min-width: 8; border: none; padding: 0 2; margin-left: 1;
        background: $panel; color: $foreground; text-style: none; }
    Button:hover { background: $panel-lighten-1; border: none; }
    Button:focus { text-style: bold; background: $panel-lighten-2; }
    Button.-primary { background: $primary; color: $background; text-style: bold; border: none; }
    Button.-primary:hover { background: $primary-lighten-1; border: none; }
    Button.-primary:focus { background: $primary-lighten-1; text-style: bold; }
    .group-label { margin-top: 1; color: $foreground 45%; text-style: bold; }
    .choice { height: 1; }
    .choice:hover { background: $foreground 6%; }
    .choice:focus { background: $primary; }
    .choice.-danger:focus { background: $error; }

    CommandPalette { background: $background 70%; }
    CommandPalette > Vertical { width: 72; max-width: 92%; margin-top: 4; background: $surface;
        border-left: outer $primary; }
    CommandPalette #--input { border: none; height: 3; padding: 0 1; }
    CommandPalette SearchIcon { display: none; }
    CommandPalette CommandInput, CommandPalette CommandInput:focus { padding-left: 1; }
    CommandPalette CommandList, CommandPalette CommandList:focus { border: none; max-height: 60vh;
        padding: 0 0 1 0; background: $surface; }
    CommandPalette CommandList > .option-list--option { padding: 0 2; }
    CommandPalette CommandList > .option-list--option-highlighted { background: $primary 22%;
        color: $foreground; text-style: none; }
    CommandPalette > .command-palette--highlight { color: $primary; text-style: bold; }
    CommandPalette > .command-palette--help-text { color: $foreground 45%; }
    Toast { background: $panel; border-left: outer $primary; padding: 0 1; }
    Toast.-error { border-left: outer $error; }
    Toast.-warning { border-left: outer $warning; }
    """
    BINDINGS = [
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("s", "sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "select", "Details", show=True),
        Binding("escape", "table", "Back", show=False),
        Binding("1", "view('market')", "Market", show=False),
        Binding("2", "view('workspaces')", "Workspaces", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        provider,
        query: str = "",
        rented: bool = False,
        disk: int = 40,
        count: int = 1,
        sort: str = "price",
        demo: bool = False,
        image: str | None = None,
    ):
        super().__init__()
        self.provider = provider
        self.gpu_query, self.rented, self.disk, self.count = query, rented, disk, count
        self.sort_mode, self.demo = sort, demo
        self.image = image or provider.default_image
        self.rows: list[Offer | Instance] = []
        self.visible_rows: list[Offer | Instance] = []
        self.loading = False
        self.spin = 0
        self.marked = None
        self.table_narrow = False
        self.selected_id = None
        self.updated = None
        self.announce = False
        for theme in THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="header"):
                yield Static(id="brand")
                yield Tab("Market", "market")
                yield Tab("Workspaces", "workspaces")
                yield Static(id="header-space")
                yield Static(id="provider")
            with Horizontal(id="searchbar"):
                yield Input(id="search")
                yield Static(id="meta")
            with Vertical(id="stage"):
                with Horizontal(id="body"):
                    yield DataTable(
                        id="offers",
                        cursor_type="row",
                        zebra_stripes=False,
                        cursor_foreground_priority="renderable",
                    )
                    yield Static("", id="inspector")
                yield Static(id="splash")
        with Horizontal(id="statusbar"):
            yield Static(id="status-left")
            yield Static(id="status-right")

    def on_mount(self):
        try:
            saved = state.load().get("theme")
        except CrigError:
            saved = None
        self.theme = saved if saved in self.available_themes else "cloudrig"
        self.theme_changed_signal.subscribe(self, self.theme_changed)
        self.setup_table()
        self.paint_chrome()
        self.apply_layout()
        self.query_one(DataTable).focus()
        self.load_rows()

    def theme_changed(self, theme: Theme):
        self.paint_chrome()
        self.setup_table()
        self.populate()
        if self.demo:
            return
        try:
            state.update(theme=theme.name)
        except (CrigError, OSError):
            pass

    # Layout and chrome

    @property
    def narrow(self) -> bool:
        return self.size.width < SIDEBAR_MIN_WIDTH

    def apply_layout(self):
        if not self.query("#shell"):
            return
        self.screen_stack[0].set_class(self.narrow, "-narrow")
        if self.narrow != self.table_narrow:
            self.setup_table()
            self.populate()
        compact = self.size.height < 30
        self.query_one("#header").styles.height = 2 if compact else 3
        self.query_one("#searchbar").styles.margin = (1, 0, 0, 0) if compact else (1, 0, 1, 0)
        self.paint_status()
        self.update_inspector()

    def on_resize(self):
        self.apply_layout()

    def paint_chrome(self):
        colors = palette(self)
        self.query_one("#brand", Static).update(
            Text.assemble(
                ("▲ ", colors["primary"]), ("cloud", colors["muted"]), ("rig", f"bold {colors['fg']}")
            )
        )
        provider = Text()
        provider.append("● ", style=colors["success"])
        provider.append(self.provider.name, style=colors["fg"])
        if self.demo:
            provider.append("  DEMO", style=f"bold {colors['warning']}")
        self.query_one("#provider", Static).update(provider)
        self.query_one("#tab-market").set_class(not self.rented, "-active")
        self.query_one("#tab-workspaces").set_class(self.rented, "-active")
        self.query_one("#search", Input).placeholder = (
            "Filter workspaces by name, GPU, region…" if self.rented else "Filter GPU, region, offer ID…"
        )
        self.paint_status()

    def paint_status(self):
        if not self.query("#statusbar"):
            return
        colors = palette(self)
        short = self.size.width < 140
        if self.demo:
            note = "demo · no charges" if short else "demo data · no account access or charges"
        elif self.rented:
            note = "live" if short else "live · privileged actions ask for sudo"
        else:
            note = "USD" if short else "on-demand · USD · rates can change before rental"
        left = Text(note, style=colors["muted"], no_wrap=True, overflow="ellipsis")
        if self.updated and not short:
            left.append(f"  ·  updated {self.updated}", style=colors["faint"])
        if self.query_one("#search", Input).has_focus:
            hints = [("enter", "apply"), ("esc", "back")]
        elif self.rented:
            hints = [("↑↓", "select"), ("enter", "actions"), ("/", "filter"), ("1", "market")]
        else:
            hints = [
                ("↑↓", "select"),
                ("enter", "review"),
                ("/", "filter"),
                ("s", "sort"),
                ("2", "workspaces"),
            ]
        hints += [("?", "help"), ("ctrl+p", "commands")]
        if self.size.width < 100 and len(hints) > 4:
            hints = hints[1:3] + hints[-1:]
        elif self.size.width < 130:
            hints = [hint for hint in hints if hint[0] != "?"]
        right = Text(no_wrap=True)
        for index, (key, label) in enumerate(hints):
            right.append("   " if index else "")
            right.append(key, style=f"bold {colors['fg']}")
            right.append(f" {label}", style=colors["muted"])
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-right", Static).update(right)

    def on_descendant_focus(self):
        self.paint_status()

    def on_descendant_blur(self):
        self.paint_status()

    def setup_table(self):
        table = self.query_one(DataTable)
        table.clear(columns=True)
        self.marked = None
        table.add_column(" ", key="mark", width=1)
        labels = (
            ("NAME", "STATUS", "GPU", "USD / HR", "REGION", "ID")
            if self.rented
            else ("GPU", "VRAM", "USD / HR", "REGION", "RELIABILITY", "OFFER")
        )
        # The sidebar shows the ID, so the column only appears when the sidebar is hidden.
        self.table_narrow = self.narrow
        sorted_label, arrow = (None, "") if self.rented else SORT_COLUMNS[self.sort_mode]
        primary = palette(self)["primary"]
        for label in labels if self.narrow else labels[:-1]:
            header = Text.assemble((label, primary), f" {arrow}") if label == sorted_label else label
            table.add_column(header, key=label)

    # Data

    @work(exclusive=True)
    async def load_rows(self):
        self.loading = True
        self.spin = 0
        timer = self.set_interval(0.08, self.tick_spinner)
        self.tick_spinner()
        try:
            if self.rented:
                self.rows = await asyncio.to_thread(self.provider.instances)
            else:
                self.rows = await asyncio.to_thread(
                    self.provider.offers, self.gpu_query, self.disk, self.count
                )
            timer.stop()
            self.loading = False
            self.updated = time.strftime("%H:%M")
            self.populate()
            if self.announce:
                noun = "workspace" if self.rented else "offer"
                count = len(self.visible_rows)
                self.notify(f"{count} {noun}{'s' * (count != 1)} loaded", title="Refreshed", timeout=2)
        except CrigError as error:
            self.show_error(str(error))
        except Exception:
            self.show_error("Could not load cloud data. Details withheld to protect credentials.")
        finally:
            timer.stop()
            self.loading = False
            self.announce = False
            self.paint_splash()

    def show_error(self, message: str):
        self.query_one("#meta", Static).update(Text("✕ load failed", style=palette(self)["error"]))
        self.notify(message, title="cloudrig", severity="error", timeout=8)

    def tick_spinner(self):
        colors = palette(self)
        frame = SPINNER[self.spin % len(SPINNER)]
        self.spin += 1
        label = "loading workspaces" if self.rented else "fetching live availability"
        self.query_one("#meta", Static).update(
            Text.assemble((frame, colors["primary"]), (f" {label}…", colors["muted"]))
        )
        self.paint_splash()

    def populate(self):
        if not self.query("#offers") or self.loading:
            return
        colors = palette(self)
        needle = self.query_one("#search", Input).value.casefold()
        selected = self.selected_id
        table = self.query_one(DataTable)
        filtered = [
            row
            for row in self.rows
            if isinstance(row, Instance) == self.rented
            and needle in f"{row.gpu} {row.region} {row.id} {getattr(row, 'name', '')}".casefold()
        ]
        if self.rented:
            self.visible_rows = sorted(filtered, key=lambda row: (row.status != "running", row.name))
        else:
            self.visible_rows = sorted_offers(filtered, self.sort_mode, self.disk)
        table.clear()
        self.marked = None
        cheapest = min(
            (hourly(row, self.disk) for row in self.visible_rows if isinstance(row, Offer)), default=None
        )
        for row in self.visible_rows:
            if isinstance(row, Offer):
                best = hourly(row, self.disk) == cheapest
                cells = [
                    "",
                    Text(row.title, style=colors["fg"]),
                    Text(f"{row.vram:g} GB", style=colors["muted"]),
                    Text(rate(row, self.disk), style=f"bold {colors['success']}" if best else colors["fg"]),
                    Text(row.region, style=colors["fg"]),
                    self.reliability(row.reliability, colors),
                    Text(row.id, style=colors["faint"]),
                ]
            else:
                mark, color = status_style(row.status, colors)
                cells = [
                    "",
                    Text(row.name, style=f"bold {colors['fg']}"),
                    Text(f"{mark} {row.status}", style=color),
                    Text(f"{row.gpu} ×{row.count}", style=colors["fg"]),
                    Text(f"${row.hourly:.3f}", style=colors["fg"]),
                    Text(row.region, style=colors["muted"]),
                    Text(row.id, style=colors["faint"]),
                ]
            table.add_row(*cells[: len(table.columns)], key=row.id)
        if selected:
            for index, row in enumerate(self.visible_rows):
                if row.id == selected:
                    table.move_cursor(row=index)
                    break
        self.paint_meta()
        self.mark_cursor()
        self.update_inspector()
        self.paint_splash()

    def reliability(self, value: float | None, colors: dict[str, str]) -> Text:
        if value is None:
            return Text("—", style=colors["faint"])
        color = (
            colors["success"] if value >= 0.995 else colors["warning"] if value >= 0.98 else colors["error"]
        )
        return Text.assemble(("● ", color), (f"{value:.1%}", colors["fg"]))

    def paint_meta(self):
        colors = palette(self)
        count = len(self.visible_rows)
        text = Text(no_wrap=True)
        text.append(f"{count}", style=f"bold {colors['fg']}")
        if self.rented:
            running = [row for row in self.visible_rows if row.status == "running"]
            text.append(f" workspace{'s' * (count != 1)}   ", style=colors["muted"])
            text.append(f"${sum(row.hourly for row in running):.3f}/hr", style=f"bold {colors['fg']}")
            text.append(" running", style=colors["muted"])
        else:
            text.append(f" offer{'s' * (count != 1)}   ", style=colors["muted"])
            text.append("sort ", style=colors["muted"])
            text.append(SORT_LABELS[self.sort_mode], style=colors["fg"])
            text.append(f"   {self.disk} GB disk", style=colors["muted"])
            if any(row.disk_hourly_gb is None for row in self.visible_rows):
                text.append("   + storage extra", style=colors["warning"])
        self.query_one("#meta", Static).update(text)

    def mark_cursor(self):
        table = self.query_one(DataTable)
        if self.marked is not None and self.marked in table.rows:
            table.update_cell(self.marked, "mark", "")
        self.marked = None
        if self.visible_rows and table.cursor_row < len(self.visible_rows):
            key = self.visible_rows[table.cursor_row].id
            table.update_cell(key, "mark", Text("▌", style=palette(self)["primary"]))
            self.marked = self.selected_id = key

    @on(Input.Changed, "#search")
    def filter_changed(self):
        self.populate()

    @on(Input.Submitted, "#search")
    def search_submitted(self):
        self.query_one(DataTable).focus()

    @on(DataTable.RowHighlighted)
    def highlight(self):
        self.mark_cursor()
        self.update_inspector()

    @on(DataTable.RowSelected)
    def row_selected(self):
        self.action_select()

    # Splash

    def paint_splash(self):
        if not self.query("#splash"):
            return
        splash = self.query_one("#splash", Static)
        failed = not self.loading and not self.rows and self.updated is None
        splash.display = self.loading or not self.visible_rows
        if not splash.display:
            return
        colors = palette(self)
        text = logo(colors) if self.query_one("#stage").size.height >= 15 else Text(justify="center")
        text.append("\nFind a cloud GPU. Rent it. Get to work.\n\n", style=colors["muted"])
        if self.loading:
            frame = SPINNER[self.spin % len(SPINNER)]
            text.append(frame, style=colors["primary"])
            subject = "your workspaces" if self.rented else "live availability"
            text.append(f" loading {subject} from {self.provider.name}…\n", style=colors["muted"])
        else:
            needle = self.query_one("#search", Input).value
            if failed:
                message = "Could not load from the provider"
            elif needle:
                message = f"Nothing matches “{needle}”"
            elif self.rented:
                message = "No workspaces yet"
            else:
                message = f"No offers for “{self.gpu_query or 'all GPUs'}”"
            text.append(f"{message}\n\n", style=f"bold {colors['fg']}")
            hints = [("esc", "clear filter")] if needle else []
            hints.append(("r", "refresh"))
            hints.append(("1", "market") if self.rented else ("2", "workspaces"))
            for index, (key, label) in enumerate(hints):
                text.append("    " if index else "")
                text.append(key, style=f"bold {colors['fg']}")
                text.append(f" {label}", style=colors["muted"])
            text.append("\n")
        text.append(f"\nv{__version__}", style=colors["faint"])
        splash.update(text)

    # Inspector

    def update_inspector(self):
        if not self.query("#inspector"):
            return
        inspector = self.query_one("#inspector", Static)
        colors = palette(self)
        row_index = self.query_one(DataTable).cursor_row
        if self.loading:
            inspector.update(Text("Loading…", style=colors["muted"]))
        elif not self.visible_rows or row_index >= len(self.visible_rows):
            inspector.update(self.empty_state(colors))
        elif self.narrow:
            inspector.update(self.summary(self.visible_rows[row_index], colors))
        elif self.rented:
            inspector.update(self.instance_card(self.visible_rows[row_index], colors))
        else:
            inspector.update(self.offer_card(self.visible_rows[row_index], colors))

    def empty_state(self, colors) -> Text:
        needle = self.query_one("#search", Input).value
        text = Text()
        if needle:
            text.append(f"Nothing matches “{needle}”\n", style=f"bold {colors['fg']}")
        elif self.rented:
            text.append("No workspaces yet\n", style=f"bold {colors['fg']}")
        else:
            text.append(f"No offers for “{self.gpu_query or 'all GPUs'}”\n", style=f"bold {colors['fg']}")
        hints = [("esc", "clear the filter")] if needle else []
        hints.append(("r", "refresh from the provider"))
        hints.append(("1", "search the market") if self.rented else ("2", "see your workspaces"))
        for key, label in hints:
            text.append(f"\n{key:<5}", style=f"bold {colors['fg']}")
            text.append(label, style=colors["muted"])
        return text

    def summary(self, row, colors) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        if isinstance(row, Offer):
            text.append(f"{row.title}  ", style=f"bold {colors['primary']}")
            text.append(f"{rate(row, self.disk)}/hr", style=f"bold {colors['fg']}")
            text.append(f"  ${hourly(row, self.disk) * 24:.2f}/day", style=colors["muted"])
            details = f"\n{row.count} × {row.vram:g} GB  ·  {row.region}"
            if row.reliability is not None:
                details += f"  ·  {row.reliability:.1%} reliable"
            text.append(details, style=colors["muted"])
            text.append("\nenter", style=f"bold {colors['fg']}")
            text.append(" review and rent  ·  selecting never charges", style=colors["muted"])
        else:
            mark, color = status_style(row.status, colors)
            text.append(f"{row.name}  ", style=f"bold {colors['primary']}")
            text.append(f"{mark} {row.status}", style=color)
            text.append(f"  ${row.hourly:.3f}/hr", style=colors["fg"])
            text.append(f"\n{row.gpu} ×{row.count}  ·  {row.region}", style=colors["muted"])
            text.append("\nenter", style=f"bold {colors['fg']}")
            text.append(" connect, transfer files, or manage", style=colors["muted"])
        return text

    def offer_card(self, row: Offer, colors) -> Text:
        width = 38
        price = hourly(row, self.disk)
        text = Text()
        text.append(f"{row.title}\n", style=f"bold {colors['primary']}")
        text.append(f"{row.region}  ·  {row.provider}", style=colors["muted"])
        text.append("\n\n")
        text.append(rate(row, self.disk), style=f"bold {colors['fg']}")
        text.append(" /hr", style=colors["muted"])
        text.append(f"\n${price * 24:.2f}", style=colors["fg"])
        text.append(" per day with ", style=colors["muted"])
        text.append(f"{self.disk} GB", style=colors["fg"])
        text.append(" disk", style=colors["muted"])
        if row.total(self.disk) is None:
            text.append("\n+ storage billed separately", style=colors["warning"])

        section(text, "Spec", width, colors)
        field(text, "GPUs", f"{row.count} × {row.gpu}", colors)
        field(text, "VRAM", f"{row.vram:g} GB each, {row.vram * row.count:g} GB total", colors)
        field(text, "Per VRAM GB", f"${price / (row.vram * row.count):.4f}/hr", colors)
        field(text, "Reliability", self.reliability(row.reliability, colors), colors)
        if row.disk_max is not None:
            field(text, "Disk", f"up to {row.disk_max:g} GB", colors)
        stock = row.availability
        color = colors["success"] if stock == "available" else colors["warning"]
        field(text, "Stock", Text(f"● {stock}", style=color), colors)
        field(text, "Offer", row.id, colors)

        prices = sorted(hourly(offer, self.disk) for offer in self.visible_rows)
        if len(prices) > 1:
            section(text, "Market", width, colors)
            low, high, bar = prices[0], prices[-1], 22
            spot = round((price - low) / (high - low) * (bar - 1)) if high > low else 0
            text.append(f"\n${low:.2f} ", style=colors["muted"])
            text.append("━" * spot, style=colors["faint"])
            text.append("●", style=f"bold {colors['primary']}")
            text.append("━" * (bar - 1 - spot), style=colors["faint"])
            text.append(f" ${high:.2f}", style=colors["muted"])
            rank = prices.index(price) + 1
            label = "Cheapest" if rank == 1 else f"#{rank} by price"
            text.append(f"\n{label} of {len(prices)} matching offers", style=colors["muted"])

        text.append("\n\n\nenter", style=f"bold {colors['fg']}")
        text.append("  review and rent\n", style=colors["muted"])
        text.append("Selecting never charges. Renting asks for sudo.", style=colors["faint"])
        return text

    def instance_card(self, row: Instance, colors) -> Text:
        width = 38
        mark, color = status_style(row.status, colors)
        text = Text()
        text.append(f"{row.name}\n", style=f"bold {colors['primary']}")
        text.append(f"{mark} {row.status}", style=color)
        text.append(f"  ·  {row.gpu} ×{row.count}", style=colors["muted"])
        text.append("\n\n")
        text.append(f"${row.hourly:.3f}", style=f"bold {colors['fg']}")
        text.append(" /hr", style=colors["muted"])
        text.append(f"\n${row.hourly * 24:.2f}", style=colors["fg"])
        text.append(" per day at this rate", style=colors["muted"])

        section(text, "Details", width, colors)
        field(text, "Region", row.region, colors)
        field(text, "Address", f"{row.host}:{row.port}" if row.host else "not assigned", colors)
        access = (
            Text("● authorized", style=colors["success"])
            if row.approved
            else Text("○ needs sudo approval", style=colors["warning"])
        )
        field(text, "Access", access, colors)
        field(text, "Provider", row.provider, colors)
        field(text, "ID", row.id, colors)

        section(text, "Actions", width, colors)
        for key, label in [
            ("enter", "open actions"),
            ("s", "ssh"),
            ("c", "VS Code"),
            ("u / d", "send / get files"),
        ]:
            text.append(f"\n{key:<7}", style=f"bold {colors['fg']}")
            text.append(label, style=colors["muted"])
        text.append("\n\nShortcuts work inside the actions dialog.", style=colors["faint"])
        return text

    # Commands

    def get_system_commands(self, screen: Screen):
        yield SystemCommand("Market", "Search GPU offers", lambda: self.action_view("market"))
        yield SystemCommand("Workspaces", "Your rented instances", lambda: self.action_view("workspaces"))
        yield SystemCommand("Refresh", "Reload from the provider", self.action_refresh)
        yield SystemCommand("Filter", "Focus the filter field", self.action_filter)
        yield SystemCommand("Keyboard shortcuts", "Show every key cloudrig understands", self.action_help)
        if not self.rented:
            for mode in SORTS:
                yield SystemCommand(
                    f"Sort by {mode}",
                    f"Order offers {SORT_LABELS[mode]}",
                    lambda mode=mode: self.set_sort(mode),
                )
        hidden = {"Maximize", "Minimize", "Screenshot", "Keys"}
        yield from (command for command in super().get_system_commands(screen) if command.title not in hidden)

    def action_filter(self):
        self.query_one("#search", Input).focus()

    def set_sort(self, mode: str):
        self.sort_mode = mode
        self.setup_table()
        self.populate()

    def action_sort(self):
        if not self.rented:
            self.set_sort(SORTS[(SORTS.index(self.sort_mode) + 1) % len(SORTS)])

    def action_refresh(self):
        self.announce = True
        self.load_rows()

    def action_help(self):
        if len(self.screen_stack) == 1:
            self.push_screen(Help())

    def action_cursor(self, step: int):
        table = self.query_one(DataTable)
        if table.has_focus:
            table.move_cursor(row=max(table.cursor_row + step, 0))

    def action_table(self):
        search = self.query_one("#search", Input)
        if not search.has_focus and search.value:
            search.value = ""
        self.query_one(DataTable).focus()

    def action_view(self, view: str):
        rented = view == "workspaces"
        if rented == self.rented or len(self.screen_stack) > 1:
            return
        self.rented = rented
        self.rows, self.visible_rows = [], []
        self.selected_id = None
        self.query_one("#search", Input).value = ""
        self.setup_table()
        self.paint_chrome()
        self.query_one(DataTable).focus()
        self.load_rows()

    def action_select(self):
        if self.loading or not self.visible_rows or len(self.screen_stack) > 1:
            return
        row = self.visible_rows[self.query_one(DataTable).cursor_row]
        if isinstance(row, Offer):
            self.push_screen(Checkout(row, self.image, self.disk), self.accept_result)
        else:
            self.push_screen(Actions(row), self.accept_result)

    def accept_result(self, result):
        if result is not None:
            self.exit(result)
