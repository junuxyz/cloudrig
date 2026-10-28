import asyncio
import math
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, DataTable, Input, Label, Static

from cloudrig import __version__, preparation, state, updates
from cloudrig.models import CrigError, Instance, InstanceAction, Offer, Rental

ACCENT = "#a5b4fc"
SORTS = ("price", "vram", "region", "gpus")
SORT_COLUMNS = {
    "price": ("price/hr", "↑"),
    "vram": ("vram", "↓"),
    "region": ("region", "↑"),
    "gpus": ("gpu", "↓"),
}
PULSE = ("●  ·  ·", "·  ●  ·", "·  ·  ●", "·  ●  ·")
SIDEBAR_MIN_WIDTH = 104
SPINNER = "⣾⣽⣻⢿⡿⣟⣯⣷"
TIMELINE_WIDTH = 57

THEMES = (
    Theme(
        name="cloudrig",
        primary=ACCENT,
        secondary="#93c5fd",
        accent=ACCENT,
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
        secondary="#2563eb",
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


def sorted_offers(offers: list[Offer], sort: str, disk: int) -> list[Offer]:
    def key(offer):
        price = hourly(offer, disk)
        order = {"price": price, "vram": -offer.vram, "region": offer.region, "gpus": -offer.count}
        return order[sort], price, offer.id

    return sorted(offers, key=key)


def rate(offer: Offer, disk: int) -> str:
    total = offer.total(disk)
    return f"${total:.3f}" if total is not None else f"${offer.hourly:.3f}+"


def hourly(offer: Offer, disk: int) -> float:
    total = offer.total(disk)
    return total if total is not None else offer.hourly


_THEME_COLORS: dict[str, tuple[Theme, dict[str, str]]] = {}


def palette(app: App) -> dict[str, str]:
    """Return theme colors for Rich text."""
    # app.theme_variables can lag a theme change by one refresh.
    theme = app.current_theme
    cached = _THEME_COLORS.get(theme.name)
    if cached is None or cached[0] is not theme:
        cached = _THEME_COLORS[theme.name] = (theme, theme.to_color_system().generate())
    values = cached[1]
    background, foreground = Color.parse(values["background"]), Color.parse(values["foreground"])
    return {
        "bg": background.hex,
        "fg": foreground.hex,
        "muted": foreground.blend(background, 0.38).hex,
        "faint": foreground.blend(background, 0.62).hex,
        "primary": values["primary"],
        "success": values["success"],
        "warning": values["warning"],
        "error": values["error"],
    }


def status_style(status: str, colors: dict[str, str]) -> tuple[str, str]:
    if status == "running":
        return "●", colors["success"]
    if status in {"stopped", "exited"}:
        return "○", colors["faint"]
    return "◐", colors["warning"]


def hints(text: Text, pairs: list[tuple[str, str]], colors: dict[str, str]) -> Text:
    for index, (key, label) in enumerate(pairs):
        text.append("   " if index else "")
        text.append(key, style=colors["fg"])
        text.append(f" {label}", style=colors["faint"])
    return text


def spinner(frame: int, colors: dict[str, str]) -> Text:
    return Text(SPINNER[frame % len(SPINNER)], style=colors["primary"])


def clock(seconds: int) -> str:
    return f"{seconds // 60}m {seconds % 60:02}s" if seconds >= 60 else f"{seconds}s"


def field(text: Text, label: str, value: str | Text, colors: dict[str, str]) -> None:
    text.append(f"\n{label:<13}", style=colors["faint"])
    if isinstance(value, Text):
        text.append_text(value)
    else:
        text.append(value, style=colors["fg"])


class EscHint(Static):
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


class PreparationDetails(Dialog):
    def __init__(self, identifier: str):
        super().__init__()
        self.identifier = identifier

    def compose(self) -> ComposeResult:
        record = self.app.preparations[self.identifier]
        with Vertical(id="preparation-details", classes="dialog"):
            yield from self.header(record["name"])
            yield Static(id="setup-current")
            yield Static(id="setup-history")
            yield Static(id="setup-log")
            with Horizontal(classes="dialog-actions"):
                yield Static(id="setup-hint")
                yield Button("Done", id="close-progress")

    def on_mount(self):
        self.update_progress()

    def update_progress(self):
        if not self.query("#setup-hint"):
            return  # The timer can fire before compose finishes.
        record = self.app.preparations.get(self.identifier)
        if not record:
            self.query_one("#setup-current", Static).update("Setup record is no longer available.")
            return
        colors = palette(self.app)
        phase, now = record["phase"], time.time()
        active = phase in {"queued", "preparing"}
        tone = {"ready": colors["success"], "failed": colors["error"], "interrupted": colors["error"]}
        label = {"queued": "queued", "preparing": "preparing", "ready": "ready"}.get(phase, "stopped")
        current = Text(record["step"], style=f"bold {colors['fg']}")
        current.append("\n")
        current.append(label, style=tone.get(phase, colors["primary"]))
        elapsed = max(0, int(record.get("finished", now) - record["started"]))
        current.append(f"   {clock(elapsed)}", style=colors["faint"])
        self.query_one("#setup-current", Static).update(current)
        events = record.get("history", [])[-8:]
        history = Text()
        for index, event in enumerate(events):
            last = index == len(events) - 1
            ends = record.get("finished", now) if last else events[index + 1]["at"]
            if not last or phase == "ready":
                mark, color, step = "✓", colors["faint"], colors["muted"]
            elif active:
                mark, color, step = (
                    SPINNER[self.app.preparation_frame % len(SPINNER)],
                    colors["primary"],
                    colors["fg"],
                )
            else:
                mark, color, step = "✕", colors["error"], colors["fg"]
            history.append(f"{mark}   ", style=color)
            history.append(f"{event['step']:<{TIMELINE_WIDTH}}", style=step)
            seconds = max(0, int(ends - event["at"]))
            step_time = clock(seconds) if seconds else "<1s"
            history.append(f"{step_time:>7}\n", style=colors["faint"])
        if not history:
            history.append("Earlier steps were not recorded for this setup.", style=colors["faint"])
        history.rstrip()
        self.query_one("#setup-history", Static).update(history)
        log = Text("log   ", style=colors["faint"])
        path = preparation.directory(self.app.provider.name, self.identifier) / "setup.log"
        if path.is_relative_to(Path.home()):
            path = "~" / path.relative_to(Path.home())
        log.append(str(path), style=colors["muted"])
        self.query_one("#setup-log", Static).update(log)
        note = "Updates live" if active else "Finished"
        self.query_one("#setup-hint", Static).update(Text(note, style=colors["faint"]))

    @on(Button.Pressed, "#close-progress")
    def close(self):
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
            with Horizontal(classes="form-row"):
                yield Label("name", classes="form-label")
                yield Input(value=f"gpu-{offer.id.split('|')[-1].lower()}", id="rental-name")
            with Horizontal(classes="form-row"):
                yield Label("disk", classes="form-label")
                yield Input(value=str(self.disk), type="integer", max_length=6, id="rental-disk")
                yield Label("GB", classes="form-unit")
            with Horizontal(classes="form-row"):
                yield Label("image", classes="form-label")
                yield Input(value=self.image, id="rental-image")
            yield Static(id="quote")
            yield Static(id="checkout-note")
            yield Static("", id="checkout-error")
            with Horizontal(classes="dialog-actions"):
                yield Static(id="next-step")
                yield Button("Cancel", id="cancel")
                yield Button("Rent GPU", variant="primary", id="rent")

    def on_mount(self):
        colors = palette(self.app)
        offer = self.offer
        provider = offer.provider + (f" {offer.cloud.lower()}" if offer.cloud else "")
        self.query_one("#checkout-title", Static).update(
            Text(f"{offer.region}   {provider}   {offer.id}", style=colors["faint"])
        )
        self.query_one("#checkout-note", Static).update(
            Text(
                "Billing starts when rented. Stopping may keep storage charges. "
                "Deleting removes the instance's files.",
                style=colors["faint"],
            )
        )
        self.query_one("#next-step", Static).update(
            Text("Sudo will check the price before rental.", style=colors["faint"])
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
        disk = int(value) if value.isdecimal() and len(value) <= 6 else 0
        offer = self.offer
        total = offer.total(disk)
        text = Text()
        if total is None:
            text.append(f"${offer.hourly:.3f}", style=f"bold {colors['fg']}")
            text.append(" / hr  plus storage", style=colors["muted"])
            text.append("\nThe storage rate is not in the GPU catalog.", style=colors["faint"])
        else:
            text.append(f"${total:.3f}", style=f"bold {colors['fg']}")
            text.append(" / hr", style=colors["muted"])
            text.append(
                f"\n${offer.hourly:.3f} compute  +  ${total - offer.hourly:.3f} storage for {disk} GB",
                style=colors["faint"],
            )
            text.append(
                f"\n${total * 24:.2f} per day   ${total * 24 * 30:,.0f} per month", style=colors["faint"]
            )
        self.query_one("#quote", Static).update(text)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#rental-name", Input).value.strip()
        value = self.query_one("#rental-disk", Input).value
        image = self.query_one("#rental-image", Input).value.strip()
        try:
            disk = int(value) if value.isdecimal() and len(value) <= 6 else 0
            rental = Rental(self.offer, name, disk, image)
            if self.offer.disk_max is not None and disk > self.offer.disk_max:
                raise CrigError(f"This offer has at most {self.offer.disk_max:g} GB disk.")
        except CrigError as error:
            self.query_one("#checkout-error", Static).update(
                Text(str(error), style=palette(self.app)["error"])
            )
            return
        self.dismiss(rental)


class Choice(Static, can_focus=True):
    """Show an action label with its permission requirement and keyboard shortcut."""

    BINDINGS = [Binding("enter", "choose", show=False)]

    def __init__(self, label: str, value: str, key: str = "", tag: str = "", danger: bool = False):
        super().__init__(id=f"do-{value}", classes="choice -danger" if danger else "choice")
        self.label, self.value, self.key, self.tag = label, value, key, tag

    def render(self) -> Text:
        colors = palette(self.app)
        focused = self.has_focus
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("› " if focused else "  ", style=colors["primary"])
        color = colors["error"] if self.has_class("-danger") else colors["fg"] if focused else colors["muted"]
        text.append(self.label, style=f"bold {color}" if focused else color)
        right = Text()
        if self.tag:
            right.append(self.tag, style=colors["faint"])
            right.append("   ")
        right.append(f"{self.key or ' '} ", style=colors["muted"] if focused else colors["faint"])
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
        manage = []
        if row.status != "running":
            manage.append(("Start instance", "start", "sudo"))
        if row.status not in {"stopped", "exited"}:
            manage.append(("Stop instance", "stop", ""))
        if row.approved:
            manage.append(("Reauthorize connection", "approve", "sudo"))
        manage.append(("Delete instance", "destroy", "sudo"))
        authorization = "" if row.approved else "sudo"
        connect = [
            ("Connect with SSH", "ssh", authorization),
            ("Open in VS Code", "code", authorization),
            ("Run an agent", "agent", authorization),
        ]
        return [
            ("connect", connect),
            ("files", [("Send files to cloud", "push", ""), ("Get files locally", "pull", "")]),
            ("manage", manage),
        ]

    def compose(self) -> ComposeResult:
        keys = {value: key for key, value in self.HOTKEYS.items()}
        with Vertical(id="instance-actions", classes="dialog"):
            yield from self.header(self.instance.name)
            yield Static(id="checkout-title", classes="dialog-subtitle")
            for title, options in self.groups():
                yield Label(title, classes="group-label")
                for label, value, tag in options:
                    yield Choice(label, value, keys.get(value, ""), tag, danger=value == "destroy")

    def on_mount(self):
        colors = palette(self.app)
        row = self.instance
        mark, color = status_style(row.status, colors)
        self.query_one("#checkout-title", Static).update(
            Text.assemble(
                (f"{mark} {row.status}", color),
                (f"   {row.gpu} ×{row.count}   {row.region}   ${row.hourly:.3f}/hr", colors["faint"]),
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
        ("↑ ↓  j k", "move"),
        ("enter", "rent a GPU, or open instance actions"),
        ("1  2", "GPU offers, Instances"),
        ("/", "filter"),
        ("s", "sort"),
        ("r", "refresh"),
        ("esc", "back, or clear the filter"),
        ("ctrl+p", "commands and themes"),
        ("q", "quit"),
    )
    ACTIONS = (
        ("s c a", "ssh, VS Code, agent"),
        ("u d", "send, get files"),
        ("p t x", "authorize, start, stop"),
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help", classes="dialog"):
            yield from self.header("Keyboard")
            yield Static(id="help-body")

    def on_mount(self):
        colors = palette(self.app)
        text = Text()
        for title, keys in (("", self.KEYS), ("in workspace actions", self.ACTIONS)):
            if title:
                text.append(f"\n{title}\n", style=colors["faint"])
            for key, label in keys:
                text.append(f"{key:<11}", style=colors["fg"])
                text.append(f"{label}\n", style=colors["muted"])
        text.append("\nDelete has no shortcut. Renting and starting ask for sudo.", style=colors["faint"])
        self.query_one("#help-body", Static).update(text)


class Tab(Static):
    def __init__(self, label: str, view: str):
        super().__init__(label, id=f"tab-{view}", classes="tab")
        self.view = view

    def on_click(self):
        self.app.action_view(self.view)


class CloudrigApp(App[Rental | InstanceAction | None]):
    TITLE = "cloudrig"
    SUB_TITLE = "GPU offers"
    CSS = """
    Screen { background: $background; color: $foreground; }
    #shell { padding: 1 4 0 4; }

    #header { height: 1; }
    #brand { width: auto; padding-right: 5; text-style: bold; }
    .tab { width: auto; padding-right: 3; color: $foreground 35%; }
    .tab:hover { color: $foreground 70%; }
    .tab.-active { color: $foreground; }
    #header-space { width: 1fr; }
    #provider { width: auto; }

    #searchbar { height: 1; margin: 2 0 1 0; }
    #search-label { width: 2; color: $foreground 30%; }
    #searchbar:focus-within #search-label { color: $primary; }
    #search { width: 1fr; height: 1; border: none; padding: 0; background: $background; }
    #search:focus { background: $background; }
    #search > .input--placeholder { color: $foreground 30%; }
    #meta { width: auto; height: 1; padding-left: 2; }

    #preparation { height: auto; max-height: 6; margin-top: 1; }
    #activity-row { height: auto; }
    #activity { width: 1fr; height: auto; max-height: 4; }
    #show-progress { width: auto; min-width: 0; height: 1; padding: 0; margin: 0 0 0 2;
        background: $background; color: $foreground 40%; }
    #show-progress:hover, #show-progress:focus { background: $background; color: $foreground; }
    #setup-current { height: auto; margin-top: 1; }
    #setup-history { height: auto; margin-top: 1; }
    #setup-log { height: auto; margin-top: 1; padding-top: 1; border-top: solid $foreground 8%; }
    #setup-hint { width: 1fr; height: 1; }
    #preparation-details .dialog-actions { margin-top: 2; }
    #stage { height: 1fr; layers: base splash; margin-top: 1; }
    #body { height: 1fr; }
    #splash { layer: splash; width: 100%; height: 100%; background: $background;
        content-align: center middle; text-align: center; }
    #offers { width: 1fr; height: 1fr; min-height: 4; background: $background; overflow-x: hidden;
        scrollbar-size-vertical: 1; }
    #offers:focus { background-tint: 0%; }
    #offers:focus > .datatable--header { background-tint: 0%; }
    Screen.-narrow #offers { overflow-x: auto; }
    #offers > .datatable--header { background: $background; color: $foreground 35%; }
    #offers > .datatable--cursor { background: $foreground 7%; text-style: bold; }
    #offers:blur > .datatable--cursor { background: $foreground 4%; text-style: none; }
    #offers > .datatable--hover { background: $foreground 3%; }
    #offers > .datatable--header-hover { background: $background; }
    #inspector { width: 42; height: 1fr; margin-left: 3; padding: 0 0 0 3;
        border-left: solid $foreground 8%; }
    Screen.-narrow #body { layout: vertical; }
    Screen.-narrow #inspector { width: 1fr; height: 4; margin: 1 0 0 0; padding: 1 0 0 0;
        border-left: none; border-top: solid $foreground 8%; }

    #statusbar { dock: bottom; height: 2; padding: 0 4 1 4; }
    #status-left { width: 1fr; }
    #status-right { width: auto; }

    Dialog { align: center middle; background: $background 80%; }
    .dialog { width: 76; height: auto; max-height: 95%; background: $surface; padding: 1 3;
        border: round $foreground 10%; overflow-y: auto; scrollbar-size-vertical: 1; }
    #instance-actions { width: 60; }
    #help { width: 56; }
    #help-body { height: auto; margin-top: 1; }
    .dialog-header { height: 1; }
    .dialog-title { width: 1fr; text-style: bold; }
    .esc { width: auto; color: $foreground 35%; }
    .esc:hover { color: $foreground; }
    .dialog-subtitle { height: auto; margin-bottom: 1; }
    .form-row { height: 1; margin-top: 1; }
    .form-label { width: 8; color: $foreground 40%; }
    .form-row:focus-within .form-label { color: $foreground; }
    .form-unit { width: auto; padding-left: 1; color: $foreground 40%; }
    .dialog Input { width: 1fr; height: 1; border: none; padding: 0 1; background: $foreground 4%; }
    .dialog Input:focus { background: $foreground 8%; }
    .dialog #rental-disk { width: 12; }
    #quote { height: auto; margin-top: 2; }
    #checkout-note { height: auto; margin-top: 1; }
    #checkout-error { height: auto; margin-top: 1; }
    .dialog-actions { height: 1; margin-top: 1; }
    #next-step { width: 1fr; height: 1; }
    Button { height: 1; min-width: 8; border: none; padding: 0 2; margin-left: 1;
        background: $surface; color: $foreground 55%; text-style: none; }
    Button:hover { color: $foreground; background: $foreground 5%; border: none; }
    Button:focus { color: $foreground; background: $foreground 8%; text-style: none; }
    Button.-primary { background: $foreground; color: $background; text-style: bold; border: none; }
    Button.-primary:hover { background: $foreground 85%; color: $background; border: none; }
    Button.-primary:focus { background: $primary; color: $background; text-style: bold; }
    .group-label { margin-top: 1; color: $foreground 30%; }
    .choice { height: 1; }
    .choice:focus { background: $foreground 6%; }

    CommandPalette { background: $background 80%; }
    CommandPalette > Vertical { width: 70; max-width: 92%; margin-top: 4; background: $surface;
        border: round $foreground 10%; }
    CommandPalette #--input { border: none; height: 3; padding: 0 1; }
    CommandPalette SearchIcon { display: none; }
    CommandPalette CommandInput, CommandPalette CommandInput:focus { padding-left: 1; }
    CommandPalette CommandList, CommandPalette CommandList:focus { border: none; max-height: 60vh;
        padding: 0 0 1 0; background: $surface; }
    CommandPalette CommandList > .option-list--option { padding: 0 2; text-style: none; }
    CommandPalette CommandList > .option-list--option-highlighted { background: $foreground 7%;
        color: $foreground; text-style: none; }
    CommandPalette > .command-palette--highlight { color: $primary; text-style: none; }
    CommandPalette > .command-palette--help-text { color: $foreground 40%; }
    Toast { background: $panel; border: none; padding: 1 2; }
    """
    BINDINGS = [
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("s", "sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("g", "progress", "Setup progress"),
        Binding("enter", "select", "Details", show=True),
        Binding("escape", "table", "Back", show=False),
        Binding("1", "view('market')", "GPU offers", show=False),
        Binding("2", "view('workspaces')", "Instances", show=False),
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
        selected_id: str | None = None,
        action_error: str | None = None,
    ):
        super().__init__()
        self.provider = provider
        self.gpu_query, self.rented, self.disk, self.count = query, rented, disk, count
        self.sort_mode, self.demo = sort, demo
        self.image = image or provider.default_image
        self.rows: list[Offer | Instance] = []
        self.visible_rows: list[Offer | Instance] = []
        self.loading = False
        self.load_error: str | None = None
        self.spin = 0
        self.table_narrow = False
        self.selected_id = selected_id
        self.action_error = action_error
        self.preparations: dict[str, dict] = {}
        self.preparation_frame = 0
        self.progress_error: str | None = None
        self.announce = False
        self.previous: dict[str, float] = {}
        for theme in THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="header"):
                yield Static("cloudrig", id="brand")
                yield Tab("GPU offers", "market")
                yield Tab("Instances", "workspaces")
                yield Static(id="header-space")
                yield Static(id="provider")
            with Horizontal(id="searchbar"):
                yield Label("/", id="search-label")
                yield Input(id="search")
                yield Static(id="meta")
            with Vertical(id="preparation"):
                with Horizontal(id="activity-row"):
                    yield Static(id="activity")
                    yield Button("details  g", id="show-progress")
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
        saved = None if self.demo else state.load().get("theme")
        self.theme = saved if saved in self.available_themes else "cloudrig"
        self.theme_changed_signal.subscribe(self, self.theme_changed)
        self.setup_table()
        self.paint_chrome()
        self.apply_layout()
        self.query_one(DataTable).focus()
        self.load_rows()
        self.paint_activity()
        self.set_interval(0.08, self.animate_preparations)
        if not self.demo:
            self.check_updates()
            self.poll_preparations()
            self.set_interval(1, self.poll_preparations)

    @work(exclusive=True, group="update-check")
    async def check_updates(self):
        version = await asyncio.to_thread(updates.check_notice)
        if version:
            self.notify(
                f"Cloudrig {version} is available. Run crig update.",
                title="Update available",
                timeout=12,
            )

    @work(exclusive=True, group="preparation-status")
    async def poll_preparations(self):
        try:
            records = await asyncio.to_thread(preparation.statuses, self.provider.name)
        except (OSError, ValueError, KeyError, TypeError, CrigError):
            self.progress_error = "Could not read local setup progress. Check crig status for details."
            self.paint_activity()
            return
        if not self.query("#preparation"):
            return
        previous = self.preparations
        self.preparations = records
        self.progress_error = None
        changed = records != previous
        became_ready = False
        if changed:
            for identifier, record in records.items():
                old_phase = previous.get(identifier, {}).get("phase")
                if old_phase in {"queued", "preparing"} and record["phase"] in {
                    "ready",
                    "failed",
                    "interrupted",
                }:
                    became_ready = became_ready or record["phase"] == "ready"
                    self.notify(
                        f"{record['name']}: {record['step']}",
                        title="Workspace ready" if record["phase"] == "ready" else "Setup needs attention",
                        severity="information" if record["phase"] == "ready" else "error",
                        timeout=8,
                    )
            self.populate()
        self.paint_activity()
        self.update_inspector()
        if isinstance(self.screen, PreparationDetails):
            self.screen.update_progress()
        if became_ready and self.rented and not self.loading:
            self.load_rows()

    def paint_activity(self):
        if not self.query("#preparation"):
            return
        colors = palette(self)
        lines = Text()
        if self.action_error:
            lines.append(self.action_error + "\n", style=colors["error"])
        if self.progress_error:
            lines.append(self.progress_error + "\n", style=colors["warning"])
        now = time.time()
        visible = {row.id for row in self.rows if isinstance(row, Instance)}
        for identifier, record in self.preparations.items():
            phase = record["phase"]
            if phase in {"failed", "interrupted"} and identifier not in visible:
                continue
            if phase == "ready" and now - record.get("finished", 0) > 60:
                continue
            elapsed = max(0, int(record.get("finished", now) - record["started"]))
            if phase in {"failed", "interrupted"}:
                mark, color = "✕", colors["error"]
            elif phase == "ready":
                mark, color = "✓", colors["success"]
            else:
                mark, color = "", ""
            if mark:
                lines.append(f"{mark}  ", style=color)
            else:
                lines.append_text(spinner(self.preparation_frame, colors))
                lines.append("  ")
            lines.append(f"{record['name']}   ", style=f"bold {colors['fg']}")
            lines.append(record["step"], style=colors["error"] if mark == "✕" else colors["muted"])
            lines.append(f"   {clock(elapsed)}\n", style=colors["faint"])
        panel = self.query_one("#activity", Static)
        self.query_one("#preparation").display = bool(lines.plain)
        panel.display = bool(lines.plain)
        lines.rstrip()
        panel.update(lines)
        self.query_one("#show-progress", Button).disabled = not self.preparations

    def instance_status(self, row: Instance, colors) -> Text:
        mark, color = status_style(row.status, colors)
        record = self.preparations.get(row.id)
        status = row.status
        if record and row.status not in {"stopped", "exited"}:
            phase = record["phase"]
            if phase in {"queued", "preparing"}:
                text = spinner(self.preparation_frame, colors)
                text.append(" preparing", style=colors["muted"])
                return text
            if phase in {"failed", "interrupted"}:
                status, mark, color = "setup failed", "✕", colors["error"]
            elif phase == "ready" and row.status == "running":
                status, mark, color = "ready", "●", colors["success"]
        return Text(f"{mark} {status}", style=color)

    def animate_preparations(self):
        self.preparation_frame += 1
        if any(record["phase"] in {"queued", "preparing"} for record in self.preparations.values()):
            self.paint_activity()
            if isinstance(self.screen, PreparationDetails):
                self.screen.update_progress()
        if not self.rented or self.loading or not self.query(DataTable):
            return
        table = self.query_one(DataTable)
        colors = palette(self)
        for row in self.visible_rows:
            record = self.preparations.get(row.id)
            if record and record["phase"] in {"queued", "preparing"}:
                table.update_cell(row.id, "status", self.instance_status(row, colors))

    @on(Button.Pressed, "#show-progress")
    def action_progress(self):
        if len(self.screen_stack) != 1 or not self.preparations:
            return
        identifier = (
            self.selected_id if self.selected_id in self.preparations else next(iter(self.preparations))
        )
        self.push_screen(PreparationDetails(identifier))

    def theme_changed(self, theme: Theme):
        self.paint_chrome()
        self.setup_table()
        self.populate()
        if self.demo:
            return
        try:
            state.update(theme=theme.name)
        except (CrigError, OSError):
            self.notify("Could not save the theme. Check local config permissions.", severity="warning")

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
        side = 2 if self.narrow else 4
        self.query_one("#shell").styles.padding = (0 if compact else 1, side, 0, side)
        self.query_one("#statusbar").styles.height = 1 if compact else 2
        self.query_one("#statusbar").styles.padding = (0, side, 0 if compact else 1, side)
        self.query_one("#searchbar").styles.margin = (1, 0, 0, 0) if compact else (2, 0, 1, 0)
        self.paint_status()
        self.update_inspector()
        self.paint_splash()

    def on_resize(self):
        self.apply_layout()

    def paint_chrome(self):
        colors = palette(self)
        provider = Text(self.provider.name, style=colors["muted"])
        if self.demo:
            provider.append("   demo", style=colors["primary"])
        self.query_one("#provider", Static).update(provider)
        for tab in self.query(Tab):
            tab.set_class((tab.view == "workspaces") == self.rented, "-active")
        self.query_one("#search", Input).placeholder = (
            "Filter by name, GPU, region" if self.rented else "Filter by GPU, region, offer ID"
        )
        self.paint_status()
        self.paint_meta()

    def paint_status(self):
        if not self.query("#statusbar"):
            return
        colors = palette(self)
        if self.query_one("#search", Input).has_focus:
            pairs = [("enter", "done"), ("esc", "back")]
        elif self.rented:
            pairs = [("↑↓", "move"), ("enter", "actions"), ("/", "filter"), ("1", "GPU offers")]
        else:
            pairs = [
                ("↑↓", "move"),
                ("enter", "Rent GPU"),
                ("/", "filter"),
                ("s", "sort"),
                ("2", "Instances"),
            ]
        if self.size.width < 100:
            pairs = pairs[1:3]
        left = hints(Text(no_wrap=True, overflow="ellipsis"), pairs, colors)
        right = hints(Text(no_wrap=True), [("?", "keys"), ("ctrl+p", "commands")], colors)
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-right", Static).update(right)

    def on_descendant_focus(self):
        self.paint_status()

    def on_descendant_blur(self):
        self.paint_status()

    def setup_table(self):
        table = self.query_one(DataTable)
        table.clear(columns=True)
        self.table_narrow = self.narrow
        # Wide layouts show IDs in the detail pane instead of the table.
        if self.rented:
            columns = [
                ("name", True),
                ("status", True),
                ("gpu", True),
                ("price/hr", True),
                ("region", True),
                ("id", False),
            ]
        else:
            columns = [
                ("gpu", True),
                ("vram", True),
                ("price/hr", True),
                ("region", True),
                ("reliability", True),
                ("offer", False),
            ]
        sorted_label, arrow = (None, "") if self.rented else SORT_COLUMNS[self.sort_mode]
        primary = palette(self)["primary"]
        for label, wide in columns:
            if wide or self.narrow:
                header = Text.assemble(label, (f" {arrow}", primary)) if label == sorted_label else label
                table.add_column(header, key=label)

    # Data

    @work(exclusive=True)
    async def load_rows(self):
        self.loading = True
        self.load_error = None
        self.spin = 0
        timer = self.set_interval(0.25, self.tick)
        self.tick()
        try:
            if self.rented:
                rows = await asyncio.to_thread(self.provider.instances)
            else:
                rows = await asyncio.to_thread(self.provider.offers, self.gpu_query, self.disk, self.count)
            self.previous = {row.id: self.price_of(row) for row in self.rows}
            self.rows = rows
            timer.stop()
            self.loading = False
            self.populate()
            if self.announce:
                count = len(self.visible_rows)
                noun = "instance" if self.rented else "offer"
                self.notify(f"{count} {noun}{'s' * (count != 1)}", title="Refreshed", timeout=2)
        except CrigError as error:
            self.load_error = str(error)
            self.notify(str(error), title="Could not load", severity="error", timeout=8)
        except Exception:
            self.load_error = "Could not read provider data. Run crig doctor, then retry."
            self.notify(
                "Details withheld to protect credentials.",
                title="Could not load",
                severity="error",
                timeout=8,
            )
        finally:
            timer.stop()
            self.loading = False
            self.announce = False
            self.paint_meta()
            self.paint_splash()

    def tick(self):
        self.spin += 1
        self.paint_meta()
        self.paint_splash()

    def price_of(self, row) -> float:
        return hourly(row, self.disk) if isinstance(row, Offer) else row.hourly

    def change(self, row, colors) -> Text:
        """A small arrow when a quote moved since the previous load."""
        before = self.previous.get(row.id)
        now = self.price_of(row)
        if before is None or math.isclose(before, now, abs_tol=0.0005):
            return Text("")
        return Text(" ↑", style=colors["error"]) if now > before else Text(" ↓", style=colors["success"])

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
        low = min((self.price_of(row) for row in self.visible_rows), default=None)
        for row in self.visible_rows:
            if isinstance(row, Offer):
                best = self.price_of(row) == low
                cells = {
                    "gpu": Text(row.title, style=colors["fg"]),
                    "vram": Text(f"{row.vram:g} GB", style=colors["muted"]),
                    "price/hr": Text.assemble(
                        (rate(row, self.disk), colors["primary"] if best else colors["fg"]),
                        self.change(row, colors),
                    ),
                    "region": Text(row.region, style=colors["muted"]),
                    "reliability": Text(
                        "Unknown" if row.reliability is None else f"{row.reliability:.1%}",
                        style=colors["muted"],
                    ),
                    "offer": Text(row.id, style=colors["faint"]),
                }
            else:
                running = row.status == "running"
                cells = {
                    "name": Text(row.name, style=colors["fg"] if running else colors["muted"]),
                    "status": self.instance_status(row, colors),
                    "gpu": Text(f"{row.gpu} ×{row.count}", style=colors["muted"]),
                    "price/hr": Text(
                        f"${row.hourly:.3f}", style=colors["fg"] if running else colors["faint"]
                    ),
                    "region": Text(row.region, style=colors["muted"]),
                    "id": Text(row.id, style=colors["faint"]),
                }
            table.add_row(*[cells[str(key.value)] for key in table.columns], key=row.id)
        if selected:
            for index, row in enumerate(self.visible_rows):
                if row.id == selected:
                    table.move_cursor(row=index)
                    break
        self.remember_cursor()
        self.paint_meta()
        self.update_inspector()
        self.paint_splash()
        self.paint_activity()

    def paint_meta(self):
        if not self.query("#meta"):
            return
        colors = palette(self)
        text = Text(no_wrap=True)
        if self.loading:
            text.append("loading", style=colors["faint"])
        elif self.rented:
            running = [row for row in self.visible_rows if row.status == "running"]
            text.append(f"{len(running)} running", style=colors["muted"])
            text.append(f"   ${sum(row.hourly for row in running):.3f}/hr", style=colors["fg"])
        else:
            count = len(self.visible_rows)
            text.append(f"{count} offer{'s' * (count != 1)}", style=colors["muted"])
            if self.visible_rows:
                text.append("   from ", style=colors["faint"])
                text.append(
                    f"${min(self.price_of(row) for row in self.visible_rows):.3f}/hr", style=colors["fg"]
                )
        self.query_one("#meta", Static).update(text)

    def remember_cursor(self):
        table = self.query_one(DataTable)
        if self.visible_rows and table.cursor_row < len(self.visible_rows):
            self.selected_id = self.visible_rows[table.cursor_row].id

    @on(Input.Changed, "#search")
    def filter_changed(self):
        self.populate()

    @on(Input.Submitted, "#search")
    def search_submitted(self):
        self.query_one(DataTable).focus()

    @on(DataTable.RowHighlighted)
    def highlight(self):
        self.remember_cursor()
        self.update_inspector()

    @on(DataTable.RowSelected)
    def row_selected(self):
        self.action_select()

    # Splash

    def paint_splash(self):
        if not self.query("#splash"):
            return
        splash = self.query_one("#splash", Static)
        splash.display = self.loading or self.load_error is not None or not self.visible_rows
        if not splash.display:
            return
        colors = palette(self)
        text = Text(justify="center")
        text.append("cloudrig\n", style=f"bold {colors['fg']}")
        text.append("GPUs on demand\n\n\n", style=colors["faint"])
        if self.loading:
            text.append(f"{PULSE[self.spin % len(PULSE)]}\n\n", style=colors["primary"])
            subject = "instances" if self.rented else "offers"
            text.append(f"Loading {subject} from {self.provider.name}", style=colors["muted"])
        elif self.load_error:
            text.append("Could not load data\n\n", style=colors["error"])
            text.append(self.load_error + "\n\n", style=colors["fg"])
            if self.rows:
                text.append("Previously loaded prices may be outdated.\n\n", style=colors["warning"])
            hints(text, [("r", "retry"), ("q", "quit")], colors)
        else:
            needle = self.query_one("#search", Input).value
            if needle:
                message = f"Nothing matches {needle!r}"
            elif self.rented:
                message = "No instances yet"
            else:
                message = f"No offers for {self.gpu_query or 'all GPUs'!r}"
            text.append(f"{message}\n\n", style=colors["fg"])
            text.append("Press ", style=colors["muted"])
            text.append("1" if self.rented else "2", style=colors["fg"])
            text.append(
                " to browse GPUs.\n\n" if self.rented else " to view instances.\n\n",
                style=colors["muted"],
            )
            pairs = [("esc", "clear")] if needle else []
            pairs += [("r", "refresh")]
            hints(text, pairs, colors)
        text.append(f"\n\n\nv{__version__}", style=colors["faint"])
        splash.update(text)

    # Inspector

    def update_inspector(self):
        if not self.query("#inspector"):
            return
        inspector = self.query_one("#inspector", Static)
        colors = palette(self)
        row_index = self.query_one(DataTable).cursor_row
        if self.loading or not self.visible_rows or row_index >= len(self.visible_rows):
            inspector.update("")
        elif self.narrow:
            inspector.update(self.summary(self.visible_rows[row_index], colors))
        elif self.rented:
            inspector.update(self.instance_card(self.visible_rows[row_index], colors))
        else:
            inspector.update(self.offer_card(self.visible_rows[row_index], colors))

    def summary(self, row, colors) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        if isinstance(row, Offer):
            text.append(f"{row.title}   ", style=colors["fg"])
            text.append(rate(row, self.disk), style=f"bold {colors['fg']}")
            text.append(f" /hr   ${hourly(row, self.disk) * 24:.2f} /day", style=colors["faint"])
            text.append(f"\n{row.count} × {row.vram:g} GB   {row.region}", style=colors["muted"])
            if row.reliability is not None:
                text.append(f"   {row.reliability:.1%} reliable", style=colors["muted"])
        else:
            mark, color = status_style(row.status, colors)
            text.append(f"{row.name}   ", style=colors["fg"])
            text.append(f"{mark} {row.status}", style=color)
            text.append(f"   ${row.hourly:.3f} /hr", style=colors["faint"])
            text.append(f"\n{row.gpu} ×{row.count}   {row.region}", style=colors["muted"])
        return text

    def offer_card(self, row: Offer, colors) -> Text:
        price = hourly(row, self.disk)
        text = Text()
        text.append(f"{row.title}\n", style=f"bold {colors['fg']}")
        text.append(f"{row.region}   {row.provider}", style=colors["faint"])
        text.append("\n\n\n")
        text.append(rate(row, self.disk), style=f"bold {colors['fg']}")
        text.append(" / hr", style=colors["muted"])
        text.append_text(self.change(row, colors))
        text.append(f"\n${price * 24:.2f} per day   ${price * 24 * 30:,.0f} per month", style=colors["faint"])
        if row.total(self.disk) is None:
            text.append("\nStorage is billed separately.", style=colors["warning"])
        else:
            text.append(f"\nincludes {self.disk} GB disk", style=colors["faint"])

        text.append("\n\n")
        field(text, "gpus", f"{row.count} × {row.gpu}", colors)
        field(text, "vram", f"{row.vram * row.count:g} GB", colors)
        field(text, "per GB vram", f"${price / (row.vram * row.count):.4f} /hr", colors)
        if row.reliability is not None:
            field(text, "reliability", f"{row.reliability:.1%}", colors)
        if row.disk_max is not None:
            field(text, "max disk", f"{row.disk_max:g} GB", colors)
        if row.availability != "available":
            field(text, "stock", Text(row.availability, style=colors["warning"]), colors)
        field(text, "offer", row.id, colors)

        prices = sorted(hourly(offer, self.disk) for offer in self.visible_rows)
        if len(prices) > 1:
            rank = prices.index(price) + 1
            text.append("\n\n")
            if rank == 1:
                text.append("Lowest price", style=colors["primary"])
                text.append(f" of {len(prices)} offers", style=colors["faint"])
            else:
                text.append(f"+${price - prices[0]:.3f}/hr", style=colors["muted"])
                text.append(f" over the lowest of {len(prices)} offers", style=colors["faint"])
        text.append("\n\n\n")
        hints(text, [("enter", "Rent GPU")], colors)
        text.append("   nothing is charged yet", style=colors["faint"])
        return text

    def instance_card(self, row: Instance, colors) -> Text:
        mark, color = status_style(row.status, colors)
        text = Text()
        text.append(f"{row.name}\n", style=f"bold {colors['fg']}")
        text.append(f"{mark} {row.status}", style=color)
        text.append(f"   {row.gpu} ×{row.count}", style=colors["faint"])
        text.append("\n\n\n")
        text.append(f"${row.hourly:.3f}", style=f"bold {colors['fg']}")
        text.append(" / hr", style=colors["muted"])
        text.append(
            f"\n${row.hourly * 24:.2f} per day   ${row.hourly * 24 * 30:,.0f} per month",
            style=colors["faint"],
        )

        text.append("\n\n")
        field(text, "region", row.region, colors)
        field(text, "address", f"{row.host}:{row.port}" if row.host else "not assigned", colors)
        access = (
            Text("authorized", style=colors["fg"])
            if row.approved
            else Text("needs sudo", style=colors["warning"])
        )
        field(text, "access", access, colors)
        if progress := self.preparations.get(row.id):
            field(text, "setup", progress["step"], colors)
            if progress["phase"] in {"failed", "interrupted"}:
                field(text, "log", str(preparation.directory(row.provider, row.id) / "setup.log"), colors)
        field(text, "provider", row.provider, colors)
        field(text, "id", row.id, colors)
        text.append("\n\n\n")
        hints(text, [("enter", "actions")], colors)
        return text

    # Commands

    def get_system_commands(self, screen: Screen):
        yield SystemCommand("GPU offers", "Search GPU offers", lambda: self.action_view("market"))
        yield SystemCommand("Instances", "Your rented instances", lambda: self.action_view("workspaces"))
        if self.preparations:
            yield SystemCommand("Setup progress", "View setup events and elapsed time", self.action_progress)
        yield SystemCommand("Refresh", "Reload from the provider", self.action_refresh)
        yield SystemCommand("Filter", "Focus the filter field", self.action_filter)
        yield SystemCommand("Keyboard shortcuts", "Show every key cloudrig understands", self.action_help)
        if not self.rented:
            for mode in SORTS:
                yield SystemCommand(
                    f"Sort by {mode}", f"Order offers by {mode}", lambda mode=mode: self.set_sort(mode)
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
        self.rows, self.visible_rows, self.previous = [], [], {}
        self.selected_id = None
        self.query_one("#search", Input).value = ""
        self.setup_table()
        self.paint_chrome()
        self.query_one(DataTable).focus()
        self.load_rows()

    def action_select(self):
        if self.loading or self.load_error or not self.visible_rows or len(self.screen_stack) > 1:
            return
        row = self.visible_rows[self.query_one(DataTable).cursor_row]
        if isinstance(row, Offer):
            self.push_screen(Checkout(row, self.image, self.disk), self.accept_result)
        else:
            self.push_screen(Actions(row), self.accept_result)

    def accept_result(self, result):
        if result is not None:
            self.exit(result)
