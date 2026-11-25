import asyncio
import math

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, DataTable, Input, Label, Static

from cloudrig import __version__, state
from cloudrig.models import CrigError, Instance, InstanceAction, Offer, Rental, offer_hourly, sorted_offers
from cloudrig.style import (
    SPINNER,
    SPINNER_INTERVAL,
    THEMES,
    css_variables,
    gb,
    gpu_label,
    is_transitional,
    palette,
    pct,
    per_day,
    per_hour,
    per_month,
    provider_label,
    status_style,
    usd,
)

SORTS = ("price", "vram", "region", "gpus")
SORT_COLUMNS = {
    "price": ("price/hr", "↑"),
    "vram": ("vram", "↓"),
    "region": ("region", "↑"),
    "gpus": ("gpu", "↓"),
}
SIDEBAR_MIN_WIDTH = 104


def rate(offer: Offer, disk: int) -> str:
    total = offer.total(disk)
    return usd(offer.hourly, partial=True) if total is None else usd(total)


def hourly(offer: Offer, disk: int) -> float:
    return offer_hourly(offer, disk)


def hints(text: Text, pairs: list[tuple[str, str]], colors: dict[str, str]) -> Text:
    for index, (key, label) in enumerate(pairs):
        text.append("   " if index else "")
        text.append(key, style=colors["ink"])
        text.append(f" {label}", style=colors["ink-3"])
    return text


def field(text: Text, label: str, value: str | Text, colors: dict[str, str]) -> None:
    text.append(f"\n{label:<13}", style=colors["ink-3"])
    if isinstance(value, Text):
        text.append_text(value)
    else:
        text.append(value, style=colors["ink"])


class EscHint(Static):
    def on_click(self) -> None:
        self.screen.dismiss(None)


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Back", show=False)]

    def header(self, title: str) -> ComposeResult:
        with Horizontal(classes="dialog-header"):
            yield Label(title, classes="dialog-title")
            yield EscHint("esc", id="back", classes="esc")

    def paint(self):
        """Repaint Rich text, which keeps the colors of the theme it was built with."""

    def action_cancel(self):
        self.dismiss(None)


class Checkout(Dialog):
    def __init__(self, offer: Offer, image: str, disk: int):
        super().__init__()
        self.offer, self.image, self.disk = offer, image, disk
        self.error = ""

    def compose(self) -> ComposeResult:
        offer = self.offer
        with Vertical(id="checkout", classes="dialog"):
            yield from self.header(f"Rent {gpu_label(offer.gpu, offer.count)}")
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
            yield Static("Billing starts when rented.", classes="dialog-note")
            yield Static("", id="checkout-error")
            with Horizontal(classes="dialog-actions"):
                yield Static("Asks for your PIN.", id="next-step")
                yield Button("Cancel", id="cancel")
                yield Button("Rent GPU", variant="primary", id="rent")

    def on_mount(self):
        self.paint()
        self.query_one("#rent", Button).focus()

    def paint(self):
        colors = palette(self.app)
        offer = self.offer
        self.query_one("#checkout-title", Static).update(
            Text(f"{offer.region}   {provider_label(offer.provider)}   {offer.id}", style=colors["ink-3"])
        )
        self.update_quote()
        error = self.query_one("#checkout-error", Static)
        error.update(Text(self.error, style=colors["error"]))
        error.display = bool(self.error)

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
            text.append(usd(offer.hourly, partial=True), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            text.append("   plus storage", style=colors["ink-3"])
            text.append("\nThe storage rate is not in the GPU catalog.", style=colors["ink-3"])
        else:
            text.append(usd(total), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            text.append(
                f"\n{usd(offer.hourly)} compute   +   {usd(total - offer.hourly)} storage for {gb(disk)}",
                style=colors["ink-3"],
            )
            text.append(f"\n{per_day(total * 24)}   {per_month(total * 24 * 30)}", style=colors["ink-3"])
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
            self.error = str(error)
            self.paint()
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
        text.append("› " if focused else "  ", style=colors["accent"])
        color = (
            colors["error"] if self.has_class("-danger") else colors["ink"] if focused else colors["ink-2"]
        )
        text.append(self.label, style=f"bold {color}" if focused else color)
        right = Text()
        if self.tag:
            right.append(self.tag, style=colors["ink-3"])
            right.append("   ")
        right.append(f"{self.key or ' '} ", style=colors["ink-2"] if focused else colors["ink-3"])
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
    HOTKEYS = {"c": "code", "x": "stop"}

    def __init__(self, instance: Instance):
        super().__init__()
        self.instance = instance

    def groups(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        manage = []
        if self.instance.status not in {"stopped", "exited"}:
            manage.append(("Stop instance", "stop", "pin"))
        manage.append(("Delete instance", "destroy", "pin"))
        groups = []
        if self.instance.status not in {"stopped", "exited", "terminated"}:
            groups.append(("connect", [("Open in VS Code", "code", "")]))
        return [*groups, ("manage", manage)]

    def compose(self) -> ComposeResult:
        keys = {value: key for key, value in self.HOTKEYS.items()}
        with Vertical(id="instance-actions", classes="dialog"):
            yield from self.header(self.instance.name)
            yield Static(id="checkout-title", classes="dialog-subtitle")
            for title, options in self.groups():
                yield Label(title, classes="group-label")
                for label, value, tag in options:
                    yield Choice(label, value, keys.get(value, ""), tag, danger=value == "destroy")
            yield Static(
                "Stopping may keep storage charges. Deleting removes the instance's files.",
                classes="dialog-note",
            )

    def on_mount(self):
        self.paint()
        self.query(Choice).first().focus()

    def paint(self):
        colors = palette(self.app)
        row = self.instance
        mark, color = status_style(row.status, colors, frame=self.app.spin)
        self.query_one("#checkout-title", Static).update(
            Text.assemble(
                (f"{mark} {row.status}", color),
                (
                    f"   {gpu_label(row.gpu, row.count)}   {row.region}   {per_hour(row.hourly)}",
                    colors["ink-3"],
                ),
            )
        )
        for choice in self.query(Choice):
            choice.refresh()

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
        ("1  2", "GPU offers, instances"),
        ("/", "filter"),
        ("s", "sort"),
        ("r", "refresh"),
        ("esc", "back, or clear the filter"),
        ("ctrl+p", "commands and themes"),
        ("q", "quit"),
    )
    ACTIONS = (
        ("c", "open in VS Code"),
        ("x", "stop instance"),
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help", classes="dialog"):
            yield from self.header("Keyboard")
            yield Static(id="help-body")

    def on_mount(self):
        self.paint()

    def paint(self):
        colors = palette(self.app)
        text = Text()
        for title, keys in (("", self.KEYS), ("in instance actions", self.ACTIONS)):
            if title:
                text.append(f"\n{title}\n", style=colors["ink-3"])
            for key, label in keys:
                text.append(f"{key:<11}", style=colors["ink"])
                text.append(f"{label}\n", style=colors["ink-2"])
        text.append(
            "\nDelete has no shortcut. Renting, stopping, and deleting ask for your PIN.",
            style=colors["ink-3"],
        )
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
    Screen { background: $background; color: $ink; }
    #shell { padding: 1 4 0 4; }

    #header { height: 1; }
    #brand { width: auto; padding-right: 5; text-style: bold; }
    .tab { width: auto; padding-right: 3; color: $ink-3; }
    .tab:hover { color: $ink-2; }
    .tab.-active { color: $ink; }
    #header-space { width: 1fr; }
    #provider { width: auto; }

    #searchbar { height: 1; margin: 2 0 1 0; }
    #search-label { width: 2; color: $ink-3; }
    #searchbar:focus-within #search-label { color: $primary; }
    #search { width: 1fr; height: 1; border: none; padding: 0; background: $background; }
    #search:focus { background: $background; }
    #search > .input--placeholder { color: $ink-3; }
    #meta { width: auto; height: 1; padding-left: 2; }

    #action-feedback { height: auto; max-height: 6; margin-top: 1; }
    #activity-row { height: auto; }
    #activity { width: 1fr; height: auto; max-height: 4; }
    #stage { height: 1fr; layers: base splash; margin-top: 1; }
    #body { height: 1fr; }
    #splash { layer: splash; width: 100%; height: 100%; background: $background;
        content-align: center middle; text-align: center; }
    #offers { width: 1fr; height: 1fr; min-height: 4; background: $background; overflow-x: hidden;
        scrollbar-size-vertical: 1; }
    #offers:focus { background-tint: 0%; }
    #offers:focus > .datatable--header { background-tint: 0%; }
    Screen.-narrow #offers { overflow-x: auto; }
    #offers > .datatable--header { background: $background; color: $ink-3; }
    #offers > .datatable--cursor { background: $tint-2; text-style: bold; }
    #offers:blur > .datatable--cursor { background: $tint-1; text-style: none; }
    #offers > .datatable--hover { background: $tint-1; }
    #offers > .datatable--header-hover { background: $background; }
    #inspector { width: 42; height: 1fr; margin-left: 3; padding: 0 0 0 3;
        border-left: solid $ink-4; }
    Screen.-narrow #body { layout: vertical; }
    Screen.-narrow #inspector { width: 1fr; height: 4; margin: 1 0 0 0; padding: 1 0 0 0;
        border-left: none; border-top: solid $ink-4; }

    #statusbar { dock: bottom; height: 3; padding: 1 4 1 4; }
    #status-left { width: 1fr; }
    #status-right { width: auto; }

    Dialog { align: center middle; background: $scrim; }
    .dialog { width: 76; height: auto; max-height: 95%; background: $surface; padding: 1 3;
        border: round $ink-4; overflow-y: auto; scrollbar-size-vertical: 1; }
    #instance-actions { width: 56; }
    #help { width: 56; }
    #help-body { height: auto; margin-top: 1; }
    .dialog-header { height: 1; }
    .dialog-title { width: 1fr; text-style: bold; }
    .esc { width: auto; color: $ink-3; }
    .esc:hover { color: $ink; }
    .dialog-subtitle { height: auto; margin-bottom: 1; }
    .dialog-note { height: auto; margin-top: 1; color: $ink-3; }
    .form-row { height: 1; margin-top: 1; }
    .form-label { width: 8; color: $ink-3; }
    .form-row:focus-within .form-label { color: $ink; }
    .form-unit { width: auto; padding-left: 1; color: $ink-3; }
    .dialog Input { width: 1fr; height: 1; border: none; padding: 0 1; background: $tint-1; }
    .dialog Input:focus { background: $tint-2; }
    .dialog #rental-disk { width: 12; }
    #quote { height: auto; margin-top: 2; }
    #checkout-error { height: auto; margin-top: 1; }
    .dialog-actions { height: 1; margin-top: 1; }
    #next-step { width: 1fr; height: 1; color: $ink-3; }
    Button { height: 1; min-width: 8; border: none; padding: 0 2; margin-left: 1;
        background: $surface; color: $ink-2; text-style: none; }
    Button:hover { color: $ink; background: $tint-1; border: none; }
    Button:focus { color: $ink; background: $tint-2; text-style: none; }
    Button.-primary { background: $foreground; color: $background; text-style: bold; border: none; }
    Button.-primary:hover { background: $primary; color: $background; border: none; }
    Button.-primary:focus { background: $primary; color: $background; text-style: bold; }
    .group-label { margin-top: 1; color: $ink-3; }
    .choice { height: 1; }
    .choice:focus { background: $tint-2; }

    CommandPalette { background: $scrim; }
    CommandPalette > Vertical { width: 70; max-width: 92%; margin-top: 4; background: $surface;
        border: round $ink-4; }
    CommandPalette #--input { border: none; height: 3; padding: 0 1; }
    CommandPalette SearchIcon { display: none; }
    CommandPalette CommandInput, CommandPalette CommandInput:focus { padding-left: 1; }
    CommandPalette CommandList, CommandPalette CommandList:focus { border: none; max-height: 60vh;
        padding: 0 0 1 0; background: $surface; }
    CommandPalette CommandList > .option-list--option { padding: 0 2; text-style: none; }
    CommandPalette CommandList > .option-list--option-highlighted { background: $tint-2;
        color: $ink; text-style: none; }
    CommandPalette > .command-palette--highlight { color: $primary; text-style: none; }
    CommandPalette > .command-palette--help-text { color: $ink-3; }
    Toast { background: $panel; border: none; padding: 1 2; }
    """
    BINDINGS = [
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("s", "sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "select", "Details", show=True),
        Binding("escape", "table", "Back", show=False),
        Binding("1", "view('market')", "GPU offers", show=False),
        Binding("2", "view('instances')", "Instances", show=False),
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
        self.loading_more = False
        self.has_more = True
        self.load_error: str | None = None
        self.spin = 0
        self.table_narrow = False
        self.selected_id = selected_id
        self.action_error = action_error
        self.announce = False
        self.previous: dict[str, float] = {}
        self._search_timer = None
        self._status_timer = None
        self._remote_query = query
        self._base_query = query
        self._load_gen = 0
        for theme in THEMES:
            self.register_theme(theme)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="header"):
                yield Static("cloudrig", id="brand")
                yield Tab("GPU offers", "market")
                yield Tab("Instances", "instances")
                yield Static(id="header-space")
                yield Static(id="provider")
            with Horizontal(id="searchbar"):
                yield Label("/", id="search-label")
                yield Input(id="search")
                yield Static(id="meta")
            with Vertical(id="action-feedback"):
                with Horizontal(id="activity-row"):
                    yield Static(id="activity")
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
        self.paint_error()
        if not self.demo:
            self.set_interval(10, self._poll_booting_instances)

    def _poll_booting_instances(self):
        if (
            self.rented
            and not self.loading
            and not self.loading_more
            and len(self.screen_stack) == 1
            and any(isinstance(row, Instance) and is_transitional(row.status) for row in self.rows)
        ):
            self.refresh_instances_silently()

    @work(exclusive=True, group="silent-refresh")
    async def refresh_instances_silently(self):
        gen = self._load_gen
        try:
            rows = await asyncio.to_thread(self.provider.instances)
            if gen != self._load_gen or not self.rented or self.loading:
                return
            self.previous = {row.id: self.price_of(row) for row in self.rows}
            self.rows = rows
            self.populate()
        except CrigError:
            pass

    def paint_error(self):
        if not self.query("#action-feedback"):
            return
        self.query_one("#action-feedback").display = bool(self.action_error)
        self.query_one("#activity", Static).update(
            Text(self.action_error or "", style=palette(self)["error"])
        )

    def instance_status(self, row: Instance, colors) -> Text:
        mark, color = status_style(row.status, colors, frame=self.spin)
        return Text(f"{mark} {row.status}", style=color)

    def _sync_status_spinner(self):
        active = (
            self.rented
            and not self.loading
            and any(isinstance(row, Instance) and is_transitional(row.status) for row in self.visible_rows)
        )
        if active and self._status_timer is None:
            self._status_timer = self.set_interval(SPINNER_INTERVAL, self._tick_status_spinner)
        elif not active and self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None

    def _tick_status_spinner(self):
        if not self.rented or self.loading:
            self._sync_status_spinner()
            return
        self.spin += 1
        colors = palette(self)
        table = self.query_one(DataTable)
        for row in self.visible_rows:
            if isinstance(row, Instance) and is_transitional(row.status):
                table.update_cell(row.id, "status", self.instance_status(row, colors))
        self.update_inspector()
        if isinstance(self.screen, Actions) and is_transitional(self.screen.instance.status):
            self.screen.paint()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(css_variables(self.current_theme, variables))
        self.theme_variables = variables
        return variables

    def theme_changed(self, theme: Theme):
        self.paint_chrome()
        self.setup_table()
        self.populate()
        for screen in self.screen_stack:
            if isinstance(screen, Dialog):
                screen.paint()
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
        # The status bar always keeps one blank row above its hints.
        self.query_one("#statusbar").styles.height = 2 if compact else 3
        self.query_one("#statusbar").styles.padding = (1, side, 0 if compact else 1, side)
        self.query_one("#searchbar").styles.margin = (1, 0, 0, 0) if compact else (2, 0, 1, 0)
        self.paint_status()
        self.update_inspector()
        self.paint_splash()

    def on_resize(self):
        self.apply_layout()

    def paint_chrome(self):
        colors = palette(self)
        provider = Text(provider_label(self.provider.name), style=colors["ink-2"])
        if self.demo:
            provider.append("   demo", style=colors["accent"])
        self.query_one("#provider", Static).update(provider)
        for tab in self.query(Tab):
            tab.set_class((tab.view == "instances") == self.rented, "-active")
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
                ("enter", "rent GPU"),
                ("/", "filter"),
                ("s", "sort"),
                ("2", "instances"),
            ]
        if self.size.width < 100:
            pairs = pairs[1:3]
        left = hints(Text(no_wrap=True, overflow="ellipsis"), pairs, colors)
        right_pairs = [("?", "keys"), ("q", "quit")]
        if self.size.width >= 100:
            right_pairs.append(("ctrl+p", "commands"))
        right = hints(Text(no_wrap=True), right_pairs, colors)
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
        primary = palette(self)["accent"]
        for label, wide in columns:
            if wide or self.narrow:
                header = Text.assemble(label, (f" {arrow}", primary)) if label == sorted_label else label
                table.add_column(header, key=label)

    # Data

    @work(exclusive=True)
    async def load_rows(self):
        self._load_gen += 1
        gen = self._load_gen
        target_rented = self.rented
        target_query = self.gpu_query
        self.loading = True
        self.loading_more = False
        self.has_more = True
        self.load_error = None
        self.spin = 0
        timer = self.set_interval(SPINNER_INTERVAL, self.tick)
        self.tick()
        try:
            if target_rented:
                rows = await asyncio.to_thread(self.provider.instances)
            else:
                rows = await asyncio.to_thread(self.provider.offers, target_query, self.disk, self.count)
            if gen != self._load_gen or self.rented != target_rented:
                return
            if not target_rented:
                self._remote_query = target_query
                self.has_more = len(rows) >= 200
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
            if gen != self._load_gen:
                return
            self.load_error = str(error)
            self.notify(str(error), title="Could not load", severity="error", timeout=8)
        except Exception:
            if gen != self._load_gen:
                return
            self.load_error = "Could not read provider data. Check your login or network, then retry."
            self.notify(
                self.load_error,
                title="Could not load",
                severity="error",
                timeout=8,
            )
        finally:
            timer.stop()
            if gen == self._load_gen:
                self.loading = False
                self.announce = False
                self.paint_meta()
                self.paint_splash()

    @work(exclusive=True, group="dynamic-offers")
    async def fetch_dynamic_offers(self, query: str, *, require_gpu_match: bool = False):
        if self.rented:
            return
        if require_gpu_match:
            matched = await asyncio.to_thread(self.provider.has_gpu_match, query)
            if not matched or self.rented:
                return
        self._load_gen += 1
        gen = self._load_gen
        self.loading_more = True
        self.paint_meta()
        try:
            rows = await asyncio.to_thread(self.provider.offers, query, self.disk, self.count)
            if gen != self._load_gen or self.rented:
                return
            self._remote_query = query
            self.gpu_query = query
            self.has_more = len(rows) >= 200
            self.load_error = None
            self.previous = {row.id: self.price_of(row) for row in self.rows}
            self.rows = rows
            self.populate()
        except CrigError as error:
            if gen != self._load_gen or self.rented:
                return
            if not self.rows:
                self.load_error = str(error)
                self.paint_splash()
            self.notify(str(error), title="Could not search", severity="error", timeout=6)
        finally:
            if gen == self._load_gen:
                self.loading_more = False
                self.paint_meta()

    @work(exclusive=True, group="load-more")
    async def load_more_offers(self):
        if self.loading or self.loading_more or not self.has_more or self.rented:
            return
        if not hasattr(self.provider, "more_offers"):
            self.has_more = False
            return
        offer_rows = [row for row in self.rows if isinstance(row, Offer)]
        if not offer_rows:
            return
        gen = self._load_gen
        after_price = max(self.price_of(row) for row in offer_rows)
        self.loading_more = True
        self.paint_meta()
        try:
            extra = await asyncio.to_thread(
                self.provider.more_offers, after_price, self._remote_query, self.disk, self.count
            )
            if gen != self._load_gen or self.rented:
                return
            existing = {row.id for row in self.rows}
            added = [row for row in extra if row.id not in existing]
            if not added or len(extra) < 50:
                self.has_more = False
            if added:
                self.rows = [*self.rows, *added]
                self.populate()
        except CrigError:
            pass
        finally:
            if gen == self._load_gen:
                self.loading_more = False
                self.paint_meta()

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
        return Text(" ↑" if now > before else " ↓", style=colors["ink-3"])

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
                    "gpu": Text(gpu_label(row.gpu, row.count), style=colors["ink"]),
                    "vram": Text(gb(row.vram), style=colors["ink-2"]),
                    "price/hr": Text.assemble(
                        (rate(row, self.disk), colors["accent"] if best else colors["ink"]),
                        self.change(row, colors),
                    ),
                    "region": Text(row.region, style=colors["ink-2"]),
                    "reliability": Text(
                        "Unknown" if row.reliability is None else pct(row.reliability),
                        style=colors["ink-2"],
                    ),
                    "offer": Text(row.id, style=colors["ink-3"]),
                }
            else:
                running = row.status == "running"
                cells = {
                    "name": Text(row.name, style=colors["ink"] if running else colors["ink-2"]),
                    "status": self.instance_status(row, colors),
                    "gpu": Text(gpu_label(row.gpu, row.count), style=colors["ink-2"]),
                    "price/hr": Text(usd(row.hourly), style=colors["ink"] if running else colors["ink-3"]),
                    "region": Text(row.region, style=colors["ink-2"]),
                    "id": Text(row.id, style=colors["ink-3"]),
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
        self.paint_error()
        self._sync_status_spinner()

    def paint_meta(self):
        if not self.query("#meta"):
            return
        colors = palette(self)
        text = Text(no_wrap=True)
        if self.loading or self.loading_more:
            text.append("loading", style=colors["ink-3"])
        elif self.rented:
            running = [row for row in self.visible_rows if row.status == "running"]
            text.append(f"{len(running)} running", style=colors["ink-2"])
            text.append(f"   {per_hour(sum(row.hourly for row in running))}", style=colors["ink"])
        else:
            count = len(self.visible_rows)
            text.append(f"{count} offer{'s' * (count != 1)}", style=colors["ink-2"])
            if self.visible_rows:
                text.append("   from ", style=colors["ink-3"])
                text.append(
                    per_hour(min(self.price_of(row) for row in self.visible_rows)), style=colors["ink"]
                )
        self.query_one("#meta", Static).update(text)

    def remember_cursor(self):
        table = self.query_one(DataTable)
        if self.visible_rows and table.cursor_row < len(self.visible_rows):
            self.selected_id = self.visible_rows[table.cursor_row].id

    @on(Input.Changed, "#search")
    def filter_changed(self):
        self.populate()
        if self.rented or not hasattr(self.provider, "has_gpu_match"):
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.35, self._maybe_dynamic_search)

    def _maybe_dynamic_search(self):
        if self.rented or self.loading or not hasattr(self.provider, "has_gpu_match"):
            return
        needle = self.query_one("#search", Input).value.strip()
        if not needle:
            if self._remote_query != self._base_query:
                self.fetch_dynamic_offers(self._base_query)
            return
        if needle != self._remote_query:
            self.fetch_dynamic_offers(needle, require_gpu_match=True)

    @on(Input.Submitted, "#search")
    def search_submitted(self):
        if self._search_timer is not None:
            self._search_timer.stop()
        needle = self.query_one("#search", Input).value.strip()
        if not self.rented and needle and needle != self._remote_query:
            if hasattr(self.provider, "has_gpu_match"):
                self.fetch_dynamic_offers(needle, require_gpu_match=True)
            elif not self.visible_rows:
                self.gpu_query = needle
                self.load_rows()
        self.query_one(DataTable).focus()

    @on(DataTable.RowHighlighted)
    def highlight(self):
        self.remember_cursor()
        self.update_inspector()
        if not self.rented and self.has_more and not self.loading_more and self.visible_rows:
            table = self.query_one(DataTable)
            if table.cursor_row >= len(self.visible_rows) - 20:
                self.load_more_offers()

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
        text.append("cloudrig\n", style=f"bold {colors['ink']}")
        text.append("GPUs on demand\n\n\n", style=colors["ink-3"])
        if self.loading:
            text.append(f"{SPINNER[self.spin % len(SPINNER)]}\n\n", style=colors["accent"])
            subject = "instances" if self.rented else "offers"
            text.append(f"Loading {subject} from {self.provider.name}", style=colors["ink-2"])
        elif self.load_error:
            text.append("Could not load data\n\n", style=colors["error"])
            text.append(self.load_error + "\n\n", style=colors["ink"])
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
            text.append(f"{message}\n\n", style=colors["ink"])
            text.append("Press ", style=colors["ink-2"])
            text.append("1" if self.rented else "2", style=colors["ink"])
            text.append(
                " to browse GPUs.\n\n" if self.rented else " to view instances.\n\n",
                style=colors["ink-2"],
            )
            pairs = [("esc", "clear")] if needle else []
            pairs += [("r", "refresh")]
            hints(text, pairs, colors)
        text.append(f"\n\n\nv{__version__}", style=colors["ink-3"])
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
            text.append(f"{gpu_label(row.gpu, row.count)}   ", style=colors["ink"])
            text.append(rate(row, self.disk), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            text.append(f"   {per_day(hourly(row, self.disk) * 24)}", style=colors["ink-3"])
            text.append(f"\n{gb(row.vram * row.count)} vram   {row.region}", style=colors["ink-2"])
            if row.reliability is not None:
                text.append(f"   {pct(row.reliability)} reliable", style=colors["ink-2"])
        else:
            mark, color = status_style(row.status, colors, frame=self.app.spin)
            text.append(f"{row.name}   ", style=colors["ink"])
            text.append(f"{mark} {row.status}", style=color)
            text.append(f"   {per_hour(row.hourly)}", style=colors["ink-3"])
            text.append(f"\n{gpu_label(row.gpu, row.count)}   {row.region}", style=colors["ink-2"])
        return text

    def offer_card(self, row: Offer, colors) -> Text:
        price = hourly(row, self.disk)
        text = Text()
        text.append(f"{gpu_label(row.gpu, row.count)}\n", style=f"bold {colors['ink']}")
        text.append(f"{row.region}   {provider_label(row.provider)}", style=colors["ink-3"])
        text.append("\n\n\n")
        text.append(rate(row, self.disk), style=f"bold {colors['ink']}")
        text.append("/hr", style=colors["ink-2"])
        text.append_text(self.change(row, colors))
        text.append(f"\n{per_day(price * 24)}   {per_month(price * 24 * 30)}", style=colors["ink-3"])
        if row.total(self.disk) is None:
            text.append("\nStorage is billed separately.", style=colors["warning"])
        else:
            text.append(f"\nincludes {gb(self.disk)} disk", style=colors["ink-3"])

        text.append("\n\n")
        field(text, "gpus", str(row.count), colors)
        field(text, "vram", gb(row.vram * row.count), colors)
        field(text, "per GB vram", per_hour(price / (row.vram * row.count), digits=4), colors)
        if row.reliability is not None:
            field(text, "reliability", pct(row.reliability), colors)
        if row.disk_max is not None:
            field(text, "max disk", gb(row.disk_max), colors)
        field(text, "offer", row.id, colors)

        prices = sorted(hourly(offer, self.disk) for offer in self.visible_rows)
        if len(prices) > 1:
            rank = prices.index(price) + 1
            text.append("\n\n")
            if rank == 1:
                text.append("Lowest", style=colors["accent"])
                text.append(f" of {len(prices)} offers", style=colors["ink-3"])
            else:
                text.append(f"+{per_hour(price - prices[0])}", style=colors["ink-2"])
                text.append(f" over the lowest of {len(prices)} offers", style=colors["ink-3"])
        text.append("\n\n\n")
        hints(text, [("enter", "rent GPU")], colors)
        text.append("   no charge yet", style=colors["ink-3"])
        return text

    def instance_card(self, row: Instance, colors) -> Text:
        mark, color = status_style(row.status, colors, frame=self.app.spin)
        text = Text()
        text.append(f"{row.name}\n", style=f"bold {colors['ink']}")
        text.append(f"{mark} {row.status}", style=color)
        text.append(f"   {gpu_label(row.gpu, row.count)}", style=colors["ink-3"])
        text.append("\n\n\n")
        text.append(usd(row.hourly), style=f"bold {colors['ink']}")
        text.append("/hr", style=colors["ink-2"])
        text.append(
            f"\n{per_day(row.hourly * 24)}   {per_month(row.hourly * 24 * 30)}", style=colors["ink-3"]
        )

        text.append("\n\n")
        field(text, "region", row.region, colors)
        field(text, "address", f"{row.host}:{row.port}" if row.host else "not assigned", colors)
        field(text, "provider", provider_label(row.provider), colors)
        field(text, "id", row.id, colors)
        text.append("\n\n\n")
        hints(text, [("enter", "actions")], colors)
        return text

    # Commands

    def get_system_commands(self, screen: Screen):
        yield SystemCommand("GPU offers", "Search GPU offers", lambda: self.action_view("market"))
        yield SystemCommand("Instances", "Your rented instances", lambda: self.action_view("instances"))
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
        rented = view == "instances"
        if rented == self.rented or len(self.screen_stack) > 1:
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        self._load_gen += 1
        self.workers.cancel_group(self, "dynamic-offers")
        self.workers.cancel_group(self, "load-more")
        self.workers.cancel_group(self, "silent-refresh")
        self.rented = rented
        self.loading_more = False
        self.gpu_query = self._base_query
        self._remote_query = self._base_query
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
