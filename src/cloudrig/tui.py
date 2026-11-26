import asyncio
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager

from rich.cells import cell_len
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SuspendNotSupported, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, DataTable, Input, Label, Static

from cloudrig import connections, state
from cloudrig.models import (
    Cancelled,
    CrigError,
    ImageTemplate,
    Instance,
    InstanceAction,
    Offer,
    Rental,
    offer_hourly,
    sorted_offers,
)
from cloudrig.style import (
    DISCLOSURE,
    FILTER_PREFIX,
    SORT_ASC,
    SORT_DESC,
    SPINNER,
    SPINNER_INTERVAL,
    STATUS_ERROR,
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
    runway,
    status_style,
    total_vram,
    usd,
)

SORTS = ("price", "vram", "region", "gpus")
SORT_COLUMNS = {
    "price": ("price/hr", SORT_ASC),
    "vram": ("vram", SORT_DESC),
    "region": ("region", SORT_ASC),
    "gpus": ("gpu", SORT_DESC),
}
SIDEBAR_MIN_WIDTH = 104
REGION_GUTTER = "  "


def rate(offer: Offer, disk: int) -> str:
    total = offer.total(disk)
    return usd(offer.hourly, partial=True) if total is None else usd(total)


def hourly(offer: Offer, disk: int) -> float:
    return offer_hourly(offer, disk)


def hints(
    text: Text,
    pairs: list[tuple[str, str]],
    colors: dict[str, str],
    *,
    key_token: str = "ink-2",
    gap: str = "  ",
) -> Text:
    for index, (key, label) in enumerate(pairs):
        if index:
            text.append(gap)
        text.append(key, style=colors[key_token])
        text.append(f" {label}", style=colors["ink-3"])
    return text


def kv_line(
    text: Text,
    label: str,
    value: str,
    width: int,
    colors: dict[str, str],
    *,
    value_token: str = "ink",
    first: bool = False,
) -> None:
    """Append a right-aligned key-value spec row inside the inspector."""
    if not first:
        text.append("\n")
    text.append(label, style=colors["ink-3"])
    pad = max(width - cell_len(label) - cell_len(value), 2)
    text.append(" " * pad)
    text.append(value, style=colors[value_token])


def truncate_plain(value: str, max_width: int) -> str:
    if max_width <= 1:
        return value[:max_width]
    if cell_len(value) <= max_width:
        return value
    out = []
    used = 0
    for ch in value:
        w = cell_len(ch)
        if used + w > max_width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + "…"


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

    def on_click(self, event) -> None:
        if event.widget is self:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class ImageOption(Static, can_focus=True):
    """A selectable Vast.ai template image inside Checkout."""

    BINDINGS = [
        Binding("enter", "choose(True)", show=False),
        Binding("space", "choose(False)", show=False),
    ]

    def __init__(self, label: str, image: str, index: int):
        super().__init__(id=f"image-preset-{index}", classes="image-option")
        self.label = label
        self.image = image

    def render(self) -> Text:
        colors = palette(self.app)
        focused = self.has_focus
        selected = self.screen.selected_image() == self.image
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"{DISCLOSURE} " if focused else "  ", style=colors["accent"])
        label_color = colors["ink"] if focused else colors["accent"] if selected else colors["ink-2"]
        short_label = truncate_plain(self.label, 16)
        text.append(f"{short_label:<18}", style=f"bold {label_color}" if focused else label_color)
        avail = max(self.size.width - text.cell_len, 8)
        image_color = colors["ink-2"] if (focused or selected) else colors["ink-3"]
        text.append(truncate_plain(self.image, avail), style=image_color)
        return text

    def on_focus(self):
        self.refresh()
        self.scroll_visible(animate=False)

    def on_blur(self):
        self.refresh()

    def on_click(self):
        self.focus()
        self.screen.apply_image_preset(self.image, advance=False)

    def action_choose(self, advance: bool = True):
        self.screen.apply_image_preset(self.image, advance=advance)


class Checkout(Dialog):
    BINDINGS = [
        Binding("down", "move_focus(1)", show=False, priority=True),
        Binding("up", "move_focus(-1)", show=False, priority=True),
    ]

    def __init__(
        self,
        offer: Offer,
        image: str,
        disk: int,
        templates: list[ImageTemplate] | None = None,
    ):
        super().__init__()
        self.offer, self.image, self.disk = offer, image, disk
        self.templates: list[ImageTemplate] = list(templates or [])
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
            with Vertical(id="image-presets"):
                for index, tpl in enumerate(self.templates):
                    yield ImageOption(tpl.name, tpl.image, index)
                yield Static("", id="image-presets-note")
            yield Static(id="quote")
            yield Static("", id="checkout-error")
            with Horizontal(classes="dialog-actions"):
                yield Static("Billing starts after PIN.", id="next-step")
                yield Button("Cancel", id="cancel")
                yield Button("Rent GPU", variant="primary", id="rent")

    def on_mount(self):
        self.paint()
        self.query_one("#rental-name", Input).focus()

    def set_templates(self, templates: list[ImageTemplate]) -> None:
        self.templates = list(templates)
        container = self.query_one("#image-presets", Vertical)
        for existing in list(container.query(ImageOption)):
            existing.remove()
        note = self.query_one("#image-presets-note", Static)
        for index, tpl in enumerate(self.templates):
            container.mount(ImageOption(tpl.name, tpl.image, index), before=note)
        self._sync_template_list()

    def selected_image(self) -> str:
        return self.query_one("#rental-image", Input).value.strip()

    def _sync_template_list(self) -> None:
        colors = palette(self.app)
        needle = self.selected_image()
        options = list(self.query(ImageOption))
        show_all = not needle or needle == self.image or any(opt.image == needle for opt in options)
        lowered = needle.casefold()
        visible = 0
        for option in options:
            match = show_all or lowered in f"{option.label} {option.image}".casefold()
            option.display = match
            if match:
                visible += 1
                option.refresh()
        container = self.query_one("#image-presets", Vertical)
        note = self.query_one("#image-presets-note", Static)
        if visible:
            note.display = False
            container.display = True
            if not show_all:
                container.scroll_home(animate=False)
        elif not self.templates and not getattr(self.app, "templates_loaded", True):
            note.update(Text("  Loading Vast.ai templates…", style=colors["ink-3"]))
            note.display = True
            container.display = True
        elif self.templates and needle:
            note.update(Text("  Custom container image", style=colors["ink-3"]))
            note.display = True
            container.display = True
        else:
            note.display = False
            container.display = False

    def apply_image_preset(self, image: str, *, advance: bool = False):
        field = self.query_one("#rental-image", Input)
        field.value = image
        self._sync_template_list()
        if advance:
            self.query_one("#rent", Button).focus()

    def _focus_chain(self) -> list:
        visible_options = [opt for opt in self.query(ImageOption) if opt.display]
        return [
            self.query_one("#rental-name", Input),
            self.query_one("#rental-disk", Input),
            self.query_one("#rental-image", Input),
            *visible_options,
            self.query_one("#rent", Button),
        ]

    def action_move_focus(self, delta: int):
        chain = self._focus_chain()
        focused = self.focused
        if focused is self.query_one("#cancel", Button):
            if delta < 0 and len(chain) >= 2:
                chain[-2].focus()
            return
        if focused in chain:
            idx = chain.index(focused)
            target = max(0, min(len(chain) - 1, idx + delta))
            chain[target].focus()
        else:
            chain[0 if delta > 0 else -1].focus()

    def on_key(self, event):
        if event.key in {"left", "right"} and isinstance(self.focused, Button):
            cancel = self.query_one("#cancel", Button)
            rent = self.query_one("#rent", Button)
            (cancel if self.focused is rent else rent).focus()
            event.stop()
            event.prevent_default()

    def paint(self):
        colors = palette(self.app)
        offer = self.offer
        self.query_one("#checkout-title", Static).update(
            Text(f"{offer.region}   {provider_label(offer.provider)}   {offer.id}", style=colors["ink-3"])
        )
        self._sync_template_list()
        self.update_quote()
        error = self.query_one("#checkout-error", Static)
        error.update(Text(self.error, style=colors["error"]))
        error.display = bool(self.error)

    @on(Input.Changed, "#rental-disk")
    def disk_changed(self):
        self.update_quote()

    @on(Input.Changed, "#rental-image")
    def image_changed(self):
        self._sync_template_list()

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
        quote_widget = self.query_one("#quote", Static)
        inner_width = max(quote_widget.content_size.width or 56, 40)
        text = Text()
        if total is None:
            text.append(usd(offer.hourly, partial=True), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            text.append("   plus storage", style=colors["warning"])
            text.append("\nThe storage rate is not in the GPU catalog.", style=colors["ink-3"])
        else:
            left_plain = f"{usd(total)}/hr"
            right_plain = f"{per_day(total * 24)}   {per_month(total * 24 * 30)}"
            text.append(usd(total), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            gap = inner_width - cell_len(left_plain) - cell_len(right_plain)
            if gap >= 3:
                text.append(" " * gap)
                text.append(right_plain, style=colors["ink-3"])
            else:
                text.append(f"\n{right_plain}", style=colors["ink-3"])
            text.append(
                f"\n{usd(offer.hourly)} compute   +   {usd(total - offer.hourly)} storage ({gb(disk)})",
                style=colors["ink-3"],
            )
        quote_widget.update(text)

    def on_resize(self):
        if self.query("#quote"):
            self.update_quote()

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
        danger = self.has_class("-danger")
        text = Text(no_wrap=True, overflow="ellipsis")
        marker_color = colors["error"] if (danger and focused) else colors["accent"]
        text.append(f"{DISCLOSURE} " if focused else "  ", style=marker_color)
        # Destructive items stay calm at rest and reveal error color on focus.
        if danger and focused:
            color = colors["error"]
        elif focused:
            color = colors["ink"]
        else:
            color = colors["ink-2"]
        text.append(self.label, style=f"bold {color}" if focused else color)
        right = Text()
        if self.tag:
            right.append(self.tag, style=colors["ink-3"])
            right.append("   ")
        right.append(f"{self.key or ' '} ", style=colors["ink"] if focused else colors["ink-3"])
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
    HOTKEYS = {"c": "code", "x": "stop", "d": "destroy"}

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
                "Stopping may keep storage charges. Deleting removes all files.",
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
                    f"  {gpu_label(row.gpu, row.count)}  {row.region}  {per_hour(row.hourly)}",
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
        ("↑ ↓  j k", "move cursor"),
        ("enter", "rent GPU, or open instance actions"),
        ("1  2", "GPU offers, instances"),
        ("/", "filter"),
        ("s", "sort offers"),
        ("r", "refresh"),
        ("esc", "back, clear filter, or dismiss error"),
        ("ctrl+p", "commands and themes"),
        ("q", "quit"),
    )
    ACTIONS = (
        ("c", "open selected instance in VS Code"),
        ("x", "stop selected instance"),
        ("d", "delete selected instance"),
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
        for title, keys in (("", self.KEYS), ("on instances", self.ACTIONS)):
            if title:
                text.append(f"\n{title}\n", style=colors["ink-3"])
            for key, label in keys:
                text.append(f"{key:<11}", style=colors["ink"])
                text.append(f"{label}\n", style=colors["ink-2"])
        text.append(
            "\nRenting, stopping, and deleting ask for your PIN.",
            style=colors["ink-3"],
        )
        self.query_one("#help-body", Static).update(text)


class Tab(Static):
    def __init__(self, label: str, view: str):
        super().__init__(label, id=f"tab-{view}", classes="tab")
        self.base_label = label
        self.view = view

    def on_click(self):
        self.app.action_view(self.view)


class CloudrigApp(App[Rental | InstanceAction | None]):
    TITLE = "cloudrig"
    SUB_TITLE = "GPU offers"
    CSS = """
    Screen { background: $canvas; color: $ink; }
    #shell { padding: 1 3 0 3; height: 1fr; }

    #header { height: 1; }
    #brand { width: auto; padding-right: 4; text-style: bold; color: $ink; }
    .tab { width: auto; padding-right: 3; color: $ink-3; }
    .tab:hover { color: $ink-2; }
    .tab.-active { color: $ink; text-style: bold; }
    #header-space { width: 1fr; }
    #provider { width: auto; }

    #body { height: 1fr; margin-top: 1; }
    #main-pane { width: 1fr; height: 1fr; }

    #searchbar { height: 1; margin: 0 0 1 0; padding: 0 1; background: $surface; }
    #searchbar:focus-within { background: $active; }
    #search-label { width: 2; color: $ink-3; }
    #searchbar:focus-within #search-label { color: $primary; }
    #search { width: 1fr; height: 1; border: none; padding: 0; background: $surface; }
    #searchbar:focus-within #search { background: $active; }
    #search:focus { background: $active; }
    #search > .input--placeholder { color: $ink-3; }
    #meta { width: auto; height: 1; padding-left: 2; }

    #action-feedback { height: auto; max-height: 4; margin-bottom: 1; padding: 0 1; background: $surface; }
    #activity-row { height: auto; }
    #activity { width: 1fr; height: auto; max-height: 4; }

    #stage { height: 1fr; layers: base splash; }
    #splash { layer: splash; width: 100%; height: 100%; background: $canvas;
        content-align: center middle; text-align: center; }

    #offers { width: 1fr; height: 1fr; min-height: 4; background: $canvas; overflow-x: hidden;
        scrollbar-size-vertical: 1; }
    #offers:focus { background-tint: 0%; }
    #offers:focus > .datatable--header { background-tint: 0%; }
    Screen.-narrow #offers { overflow-x: auto; }
    #offers > .datatable--header { background: $canvas; color: $ink-3; }
    #offers > .datatable--cursor { background: $active; text-style: bold; }
    #offers:blur > .datatable--cursor { background: $hover; text-style: none; }
    #offers > .datatable--hover { background: $hover; }
    #offers > .datatable--header-hover { background: $canvas; }

    #inspector-panel { width: 38; height: 1fr; margin-left: 2; padding: 1 2; background: $surface; }
    #inspector { width: 1fr; height: 1fr; overflow-y: auto; scrollbar-size-vertical: 1; }
    #inspector-cta { width: 1fr; height: 1; margin-top: 1; }

    Screen.-narrow #body { layout: vertical; }
    Screen.-narrow #inspector-panel { width: 1fr; height: 4; margin: 1 0 1 0; padding: 1 2;
        background: $surface; }
    Screen.-narrow #inspector { overflow-y: hidden; }
    Screen.-narrow #inspector-cta { display: none; }

    #statusbar { dock: bottom; height: 2; padding: 0 3 1 3; background: $canvas; }
    #status-left { width: 1fr; }
    #status-right { width: auto; }

    Dialog { align: center middle; background: $scrim; }
    .dialog { width: 66; height: auto; max-height: 92%; background: $elevated; padding: 2 3;
        border: none; overflow-y: auto; scrollbar-size-vertical: 1; }
    #instance-actions { width: 56; }
    #help { width: 56; }
    #help-body { height: auto; margin-top: 1; }
    .dialog-header { height: 1; }
    .dialog-title { width: 1fr; text-style: bold; color: $ink; }
    .esc { width: auto; color: $ink-3; }
    .esc:hover { color: $ink; }
    .dialog-subtitle { height: auto; margin-bottom: 1; }
    .dialog-note { height: auto; margin-top: 1; color: $ink-3; }
    .form-row { height: 1; margin-top: 1; }
    .form-label { width: 8; color: $ink-3; }
    .form-row:focus-within .form-label { color: $ink; }
    .form-unit { width: auto; padding-left: 1; color: $ink-3; }
    .dialog Input { width: 1fr; height: 1; border: none; padding: 0 1; background: $surface; color: $ink; }
    .dialog Input:focus { background: $active; }
    .dialog #rental-disk { width: 12; }
    #image-presets { height: 5; margin: 1 0 0 8; overflow-y: auto;
        scrollbar-size-vertical: 1; }
    .image-option { height: 1; }
    .image-option:hover { background: $hover; }
    .image-option:focus { background: $active; }
    #image-presets-note { height: 1; }
    #quote { height: auto; margin-top: 1; padding: 1 2; background: $surface; }
    #checkout-error { height: auto; margin-top: 1; padding: 0 1; background: $surface; }
    .dialog-actions { height: 1; margin-top: 1; }
    #next-step { width: 1fr; height: 1; color: $ink-3; }
    Button { height: 1; min-width: 8; border: none; padding: 0 2; margin-left: 1;
        background: $surface; color: $ink-2; text-style: none; }
    Button:hover { color: $ink; background: $hover; border: none; }
    Button:focus { color: $ink; background: $active; text-style: none; }
    Button.-primary { background: $ink; color: $canvas; text-style: bold; border: none; }
    Button.-primary:hover { background: $primary; color: $canvas; border: none; }
    Button.-primary:focus { background: $primary; color: $canvas; text-style: bold; }
    .group-label { margin-top: 1; color: $ink-3; }
    .choice { height: 1; }
    .choice:focus { background: $active; }

    CommandPalette { background: $scrim; }
    CommandPalette > Vertical { width: 66; max-width: 92%; margin-top: 4; background: $elevated;
        border: none; padding: 1 1; }
    CommandPalette #--input { border: none; height: 3; padding: 0 1; background: $surface; }
    CommandPalette SearchIcon { display: none; }
    CommandPalette CommandInput, CommandPalette CommandInput:focus { padding-left: 1; background: $surface; }
    CommandPalette CommandList, CommandPalette CommandList:focus { border: none; max-height: 60vh;
        padding: 1 0 0 0; background: $elevated; }
    CommandPalette CommandList > .option-list--option { padding: 0 2; text-style: none; }
    CommandPalette CommandList > .option-list--option-highlighted { background: $active;
        color: $ink; text-style: none; }
    CommandPalette > .command-palette--highlight { color: $primary; text-style: none; }
    CommandPalette > .command-palette--help-text { color: $ink-3; }
    Toast { background: $elevated; border: none; padding: 1 2; }
    """
    BINDINGS = [
        Binding("slash", "filter", "Filter", key_display=FILTER_PREFIX),
        Binding("s", "sort", "Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "select", "Details", show=True),
        Binding("escape", "table", "Back", show=False),
        Binding("1", "view('market')", "GPU offers", show=False),
        Binding("2", "view('instances')", "Instances", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
        Binding("c", "quick_action('code')", show=False),
        Binding("x", "quick_action('stop')", show=False),
        Binding("d", "quick_action('destroy')", show=False),
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
        action_runner: Callable[[Rental | InstanceAction], tuple[str | None, str | None]] | None = None,
        handoff_connect: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.provider = provider
        self.gpu_query, self.rented, self.disk, self.count = query, rented, disk, count
        self.sort_mode, self.demo = sort, demo
        self.image = image or provider.default_image
        self.action_runner = action_runner
        self.handoff_connect = handoff_connect
        self._connecting: dict[str, tuple[str, str]] = {}
        self._connect_cancels: dict[str, threading.Event] = {}
        self.rows: list[Offer | Instance] = []
        self.visible_rows: list[Offer | Instance] = []
        self.loading = False
        self.loading_more = False
        self.syncing = False
        self.has_more = True
        self.load_error: str | None = None
        self.spin = 0
        self.table_narrow = False
        self._table_width = 0
        self._col_widths: dict[str, int] = {}
        self.selected_id = selected_id
        self.action_error = action_error
        self.announce = False
        self.previous: dict[str, float] = {}
        self._search_timer = None
        self._status_timer = None
        self._remote_query = query
        self._base_query = query
        self._load_gen = 0
        self.budget: float | None = None
        self._burn_hourly: float | None = None
        self._running_count: int | None = None
        self.templates: list[ImageTemplate] = []
        self.templates_loaded = False
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
            with Horizontal(id="body"):
                with Vertical(id="main-pane"):
                    with Horizontal(id="searchbar"):
                        yield Label(FILTER_PREFIX, id="search-label")
                        yield Input(id="search")
                        yield Static(id="meta")
                    with Vertical(id="action-feedback"):
                        with Horizontal(id="activity-row"):
                            yield Static(id="activity")
                    with Vertical(id="stage"):
                        yield DataTable(
                            id="offers",
                            cursor_type="row",
                            zebra_stripes=False,
                            cursor_foreground_priority="renderable",
                        )
                        yield Static(id="splash")
                with Vertical(id="inspector-panel"):
                    yield Static("", id="inspector")
                    yield Static("", id="inspector-cta")
        with Horizontal(id="statusbar"):
            yield Static(id="status-left")
            yield Static(id="status-right")

    def on_mount(self):
        saved = None if self.demo else state.load().get("theme")
        self.theme = saved if saved in self.available_themes else "cloudrig"
        self.theme_changed_signal.subscribe(self, self.theme_changed)
        self.apply_layout()
        self.query_one(DataTable).focus()
        self.load_rows()
        self.load_budget()
        self.load_templates()
        self.paint_error()
        if not self.demo:
            self.set_interval(10, self._poll_booting_instances)

    @work(exclusive=True, group="budget")
    async def load_budget(self, *, fresh: bool = False):
        if not hasattr(self.provider, "balance"):
            return
        try:
            if fresh and hasattr(self.provider, "_balance_cache"):
                amount = await asyncio.to_thread(self.provider.balance, fresh=True)
            else:
                amount = await asyncio.to_thread(self.provider.balance)
            self.budget = amount
            if self._burn_hourly is None and not self.rented:
                instances = await asyncio.to_thread(self.provider.instances)
                self._update_instance_metrics(instances)
            self.paint_chrome()
            self.update_inspector()
        except CrigError:
            pass

    @work(exclusive=True, group="templates")
    async def load_templates(self):
        if not hasattr(self.provider, "templates"):
            self.templates_loaded = True
            return
        try:
            self.templates = await asyncio.to_thread(self.provider.templates)
        except CrigError:
            self.templates = []
        finally:
            self.templates_loaded = True
            if isinstance(self.screen, Checkout):
                self.screen.set_templates(self.templates)

    def _update_instance_metrics(self, instances: list[Instance]) -> None:
        self._burn_hourly = sum(
            row.hourly for row in instances if row.status not in {"terminated", "deleted"}
        )
        self._running_count = sum(1 for row in instances if row.status == "running")

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
        self.syncing = True
        self.paint_status()
        try:
            rows = await asyncio.to_thread(self.provider.instances)
            if gen != self._load_gen or not self.rented or self.loading:
                return
            self.previous = {row.id: self.price_of(row) for row in self.rows}
            self.rows = rows
            self._update_instance_metrics(rows)
            self.paint_chrome()
            self.populate()
        except CrigError:
            pass
        finally:
            self.syncing = False
            self.paint_status()

    def paint_error(self):
        if not self.query("#action-feedback"):
            return
        show = bool(self.action_error or self._connecting)
        self.query_one("#action-feedback").display = show
        if not show:
            self.query_one("#activity", Static).update("")
            return
        colors = palette(self)
        text = Text()
        if self.action_error:
            text.append(f"{STATUS_ERROR} ", style=colors["error"])
            text.append(self.action_error, style=colors["error"])
        for index, (_ident, (name, stage)) in enumerate(self._connecting.items()):
            if self.action_error or index:
                text.append("\n")
            text.append(f"{SPINNER[self.spin % len(SPINNER)]} ", style=colors["warning"])
            text.append(f"{name}: ", style=colors["ink"])
            text.append(stage, style=colors["ink-2"])
            if "Opening VS Code" not in stage:
                text.append("   will open VS Code when ready", style=colors["ink-3"])
        self.query_one("#activity", Static).update(text)

    def instance_status(self, row: Instance, colors) -> Text:
        mark, color = status_style(row.status, colors, frame=self.spin)
        return Text(f"{mark} {row.status}", style=color)

    def _sync_status_spinner(self):
        has_booting = (
            self.rented
            and not self.loading
            and any(isinstance(row, Instance) and is_transitional(row.status) for row in self.visible_rows)
        )
        active = has_booting or bool(self._connecting)
        if active and self._status_timer is None:
            self._status_timer = self.set_interval(SPINNER_INTERVAL, self._tick_status_spinner)
        elif not active and self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None

    def _tick_status_spinner(self):
        has_booting = (
            self.rented
            and not self.loading
            and any(isinstance(row, Instance) and is_transitional(row.status) for row in self.visible_rows)
        )
        if not has_booting and not self._connecting:
            self._sync_status_spinner()
            return
        self.spin += 1
        colors = palette(self)
        if has_booting:
            table = self.query_one(DataTable)
            for row in self.visible_rows:
                if isinstance(row, Instance) and is_transitional(row.status):
                    table.update_cell(row.id, "status", self.instance_status(row, colors))
            self.update_inspector()
            if isinstance(self.screen, Actions) and is_transitional(self.screen.instance.status):
                self.screen.paint()
        if self._connecting:
            self.paint_error()

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

    def _target_table_width(self) -> int:
        side = 2 if self.narrow else 3
        inner = max(self.size.width - side * 2, 40)
        if self.narrow:
            return inner
        # Reserve 38 cols for the matte inspector + 2 cols open canvas gap.
        return max(inner - 40, 48)

    def apply_layout(self):
        if not self.query("#shell"):
            return
        self.screen_stack[0].set_class(self.narrow, "-narrow")
        compact_height = self.size.height < 28
        side = 2 if self.narrow else 3
        self.query_one("#shell").styles.padding = (0 if compact_height else 1, side, 0, side)
        self.query_one("#body").styles.margin = (0 if compact_height else 1, 0, 0, 0)
        self.query_one("#searchbar").styles.margin = (0, 0, 0 if compact_height else 1, 0)
        self.query_one("#statusbar").styles.height = 1 if compact_height else 2
        self.query_one("#statusbar").styles.padding = (0, side, 0 if compact_height else 1, side)

        target_width = self._target_table_width()
        if self.narrow != self.table_narrow or target_width != self._table_width or not self._col_widths:
            self._table_width = target_width
            self.setup_table()
            self.populate()
        self.paint_chrome()
        self.update_inspector()
        self.paint_splash()

    def on_resize(self):
        self.apply_layout()

    def _runway_hours(self) -> float | None:
        if self.budget is None:
            return None
        burn = self._burn_hourly or 0.0
        return self.budget / burn if burn > 0 else None

    def paint_chrome(self):
        colors = palette(self)
        provider = Text(no_wrap=True)
        if self.budget is not None:
            hours = self._runway_hours()
            budget_color = (
                colors["error"]
                if self.budget <= 0
                else colors["warning"]
                if self.budget < 1.0 or (hours is not None and hours < 2.0)
                else colors["ink"]
            )
            provider.append(usd(self.budget, digits=2), style=budget_color)
            if hours is not None:
                runway_color = colors["warning"] if hours < 2.0 else colors["ink-3"]
                provider.append(f"  {runway(hours, include_eta=not self.narrow)}", style=runway_color)
            if self.demo and not self.narrow:
                provider.append("   demo", style=colors["accent"])
        else:
            provider.append(provider_label(self.provider.name), style=colors["ink-3"])
            if self.demo:
                provider.append("   demo", style=colors["accent"])
        self.query_one("#provider", Static).update(provider)

        for tab in self.query(Tab):
            active = (tab.view == "instances") == self.rented
            tab.set_class(active, "-active")
            label_style = f"bold {colors['ink']}" if active else colors["ink-3"]
            tab_text = Text(tab.base_label, style=label_style)
            if tab.view == "instances" and self._running_count:
                tab_text.append(f" {self._running_count}", style=colors["ink-2"])
            tab.update(tab_text)

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
            pairs = [("enter", "apply"), ("esc", "cancel")]
        elif self.rented:
            pairs = [
                ("↑↓", "move"),
                ("enter", "actions"),
                (FILTER_PREFIX, "filter"),
                ("c", "code"),
                ("x", "stop"),
                ("d", "delete"),
                ("1", "offers"),
            ]
        else:
            pairs = [
                ("↑↓", "move"),
                ("enter", "rent"),
                (FILTER_PREFIX, "filter"),
                ("s", "sort"),
                ("2", "instances"),
            ]
        if self.size.width < 100:
            pairs = pairs[1:4]
        left = hints(Text(no_wrap=True, overflow="ellipsis"), pairs, colors, key_token="ink-2")
        right = Text(no_wrap=True)
        if self.syncing:
            right.append(f"{SPINNER[self.spin % len(SPINNER)]} syncing   ", style=colors["ink-3"])
        right_pairs = [("?", "keys"), ("q", "quit")]
        if self.size.width >= 100:
            right_pairs = [("?", "keys"), ("ctrl+p", "commands"), ("q", "quit")]
        hints(right, right_pairs, colors, key_token="ink-2")
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-right", Static).update(right)

    def on_descendant_focus(self):
        self.paint_status()

    def on_descendant_blur(self):
        self.paint_status()

    def _compute_columns(self) -> list[tuple[str, str, int]]:
        """Return (key, justify, content_width) spanning the full table pane width."""
        available = max(self._table_width or self._target_table_width(), 44)
        # Reserve 1 cell on the right for the vertical scrollbar so rows never clip.
        usable = available - 1
        if self.rented:
            if self.narrow:
                specs = [
                    ("name", "left", 12, 1.0),
                    ("status", "left", 11, 0.0),
                    ("gpu", "left", 11, 0.8),
                    ("price/hr", "right", 9, 0.0),
                    ("region", "left", 12, 0.9),
                    ("id", "right", 7, 0.0),
                ]
            else:
                specs = [
                    ("name", "left", 14, 1.0),
                    ("status", "left", 12, 0.0),
                    ("gpu", "left", 13, 0.9),
                    ("price/hr", "right", 10, 0.0),
                    ("region", "left", 15, 1.1),
                ]
        else:
            if self.narrow:
                specs = [
                    ("gpu", "left", 13, 1.1),
                    ("vram", "right", 7, 0.0),
                    ("price/hr", "right", 11, 0.0),
                    ("region", "left", 13, 1.0),
                    ("rel", "right", 6, 0.0),
                    ("offer", "right", 6, 0.0),
                ]
            else:
                specs = [
                    ("gpu", "left", 16, 1.2),
                    ("vram", "right", 7, 0.0),
                    ("price/hr", "right", 11, 0.0),
                    ("region", "left", 16, 1.2),
                    ("rel", "right", 6, 0.0),
                ]

        # DataTable applies cell_padding (default 1) on both sides of each column.
        pad_total = 2 * len(specs)
        min_total = sum(item[2] for item in specs) + pad_total
        extra = max(usable - min_total, 0)
        flex_sum = sum(item[3] for item in specs)

        widths: list[int] = []
        allocated = 0
        flex_indices = [i for i, item in enumerate(specs) if item[3] > 0]
        for _, _, min_w, weight in specs:
            add = int(extra * (weight / flex_sum)) if (extra and flex_sum and weight > 0) else 0
            widths.append(min_w + add)
            allocated += add
        if extra > allocated and flex_indices:
            widths[flex_indices[0]] += extra - allocated

        return [(specs[i][0], specs[i][1], widths[i]) for i in range(len(specs))]

    def setup_table(self):
        table = self.query_one(DataTable)
        table.clear(columns=True)
        self.table_narrow = self.narrow
        columns = self._compute_columns()
        self._col_widths = {key: width for key, _, width in columns}

        sorted_label, arrow = (None, "") if self.rented else SORT_COLUMNS[self.sort_mode]
        colors = palette(self)
        for key, justify, width in columns:
            display = f"{REGION_GUTTER}{key}" if key == "region" else key
            if key == sorted_label:
                header = Text.assemble(display, (f" {arrow}", colors["accent"]), justify=justify)
            else:
                header = Text(display, justify=justify)
            table.add_column(header, key=key, width=width)

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
            if target_rented:
                self._update_instance_metrics(rows)
                self.paint_chrome()
            else:
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
        return Text(f" {SORT_ASC if now > before else SORT_DESC}", style=colors["ink-3"])

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
        region_w = max(self._col_widths.get("region", 16) - len(REGION_GUTTER), 6)
        gpu_w = max(self._col_widths.get("gpu", 16), 8)
        name_w = max(self._col_widths.get("name", 14), 8)

        for row in self.visible_rows:
            if isinstance(row, Offer):
                best = self.price_of(row) == low
                price_cell = Text.assemble(
                    (rate(row, self.disk), colors["accent"] if best else colors["ink"]),
                    self.change(row, colors),
                    justify="right",
                )
                cells = {
                    "gpu": Text(truncate_plain(gpu_label(row.gpu, row.count), gpu_w), style=colors["ink"]),
                    "vram": Text(total_vram(row.vram, row.count), style=colors["ink-2"], justify="right"),
                    "price/hr": price_cell,
                    "region": Text(
                        f"{REGION_GUTTER}{truncate_plain(row.region, region_w)}", style=colors["ink-2"]
                    ),
                    "rel": Text(
                        "–" if row.reliability is None else pct(row.reliability),
                        style=colors["ink-2"],
                        justify="right",
                    ),
                    "offer": Text(row.id, style=colors["ink-3"], justify="right"),
                }
            else:
                running = row.status == "running"
                stopped = row.status in {"stopped", "exited"}
                name_style = colors["ink"] if running else colors["ink-3"] if stopped else colors["ink-2"]
                attr_style = colors["ink-3"] if stopped else colors["ink-2"]
                price_style = colors["ink"] if running else colors["ink-3"]
                cells = {
                    "name": Text(truncate_plain(row.name, name_w), style=name_style),
                    "status": self.instance_status(row, colors),
                    "gpu": Text(truncate_plain(gpu_label(row.gpu, row.count), gpu_w), style=attr_style),
                    "price/hr": Text(usd(row.hourly), style=price_style, justify="right"),
                    "region": Text(
                        f"{REGION_GUTTER}{truncate_plain(row.region, region_w)}", style=attr_style
                    ),
                    "id": Text(row.id, style=colors["ink-3"], justify="right"),
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
            text.append(f"{SPINNER[self.spin % len(SPINNER)]} ", style=colors["accent"])
            text.append("loading", style=colors["ink-3"])
        elif self.rented:
            running = [row for row in self.visible_rows if row.status == "running"]
            text.append(f"{len(running)} running", style=colors["ink-2"])
            text.append(f"   {per_hour(sum(row.hourly for row in running))}", style=colors["ink"])
        else:
            count = len(self.visible_rows)
            if not self.narrow:
                text.append(f"{gb(self.disk)}   ", style=colors["ink-3"])
            text.append(f"{count} offer{'s' * (count != 1)}", style=colors["ink-2"])
            if self.visible_rows:
                text.append(
                    f"   {per_hour(min(self.price_of(row) for row in self.visible_rows))}",
                    style=colors["ink"],
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

    # Empty, initial load, and error overlay (never a brand splash on refresh)

    def paint_splash(self):
        if not self.query("#splash"):
            return
        splash = self.query_one("#splash", Static)
        has_rows = bool(self.visible_rows)
        # Keep existing rows visible during refreshes; only overlay when the table is empty or errored.
        show_loading = self.loading and not has_rows
        show_error = self.load_error is not None and not has_rows
        show_empty = not self.loading and self.load_error is None and not has_rows
        splash.display = show_loading or show_error or show_empty
        if not splash.display:
            return
        colors = palette(self)
        text = Text(justify="center")
        if show_loading:
            subject = "instances" if self.rented else "offers"
            text.append(f"{SPINNER[self.spin % len(SPINNER)]} ", style=colors["accent"])
            text.append(
                f"Loading {subject} from {provider_label(self.provider.name)}", style=colors["ink-2"]
            )
        elif show_error:
            text.append("Could not load data\n\n", style=colors["error"])
            text.append((self.load_error or "") + "\n\n", style=colors["ink-2"])
            hints(text, [("r", "retry"), ("q", "quit")], colors, key_token="ink")
        else:
            needle = self.query_one("#search", Input).value
            if needle:
                message = f"No matches for {needle!r}"
            elif self.rented:
                message = "No instances yet"
            else:
                message = f"No offers for {self.gpu_query or 'all GPUs'!r}"
            text.append(f"{message}\n\n", style=colors["ink"])
            pairs = [("esc", "clear")] if needle else []
            pairs.append(("1", "browse GPUs") if self.rented else ("2", "instances"))
            pairs.append(("r", "refresh"))
            hints(text, pairs, colors, key_token="ink")
        splash.update(text)

    # Inspector

    def update_inspector(self):
        if not self.query("#inspector"):
            return
        inspector = self.query_one("#inspector", Static)
        cta = self.query_one("#inspector-cta", Static)
        colors = palette(self)
        row_index = self.query_one(DataTable).cursor_row
        if self.loading or not self.visible_rows or row_index >= len(self.visible_rows):
            inspector.update("")
            cta.update("")
            return
        row = self.visible_rows[row_index]
        if self.narrow:
            inspector.update(self.summary(row, colors))
            cta.update("")
            return
        inner_width = max(inspector.content_size.width or 34, 28)
        if self.rented:
            inspector.update(self.instance_card(row, colors, inner_width))
            cta.update(self.instance_cta(row, colors))
        else:
            inspector.update(self.offer_card(row, colors, inner_width))
            cta.update(self.offer_cta(colors, inner_width))

    def summary(self, row, colors) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        if isinstance(row, Offer):
            text.append(f"{gpu_label(row.gpu, row.count)}   ", style=colors["ink"])
            text.append(rate(row, self.disk), style=f"bold {colors['ink']}")
            text.append("/hr", style=colors["ink-2"])
            text.append(f"   {per_day(hourly(row, self.disk) * 24)}", style=colors["ink-3"])
            text.append(f"\n{total_vram(row.vram, row.count)} vram   {row.region}", style=colors["ink-2"])
            if row.reliability is not None:
                text.append(f"   {pct(row.reliability)}", style=colors["ink-2"])
        else:
            mark, color = status_style(row.status, colors, frame=self.app.spin)
            text.append(f"{row.name}   ", style=colors["ink"])
            text.append(f"{mark} {row.status}", style=color)
            text.append(f"   {per_hour(row.hourly)}", style=colors["ink-3"])
            text.append(f"\n{gpu_label(row.gpu, row.count)}   {row.region}", style=colors["ink-2"])
        return text

    def offer_card(self, row: Offer, colors, width: int) -> Text:
        price = hourly(row, self.disk)
        text = Text()
        text.append(f"{gpu_label(row.gpu, row.count)}\n", style=f"bold {colors['ink']}")
        text.append(f"{row.region}   {provider_label(row.provider)}", style=colors["ink-3"])

        text.append("\n\n")
        text.append(rate(row, self.disk), style=f"bold {colors['ink']}")
        text.append("/hr", style=colors["ink-2"])
        text.append_text(self.change(row, colors))
        text.append(f"\n{per_day(price * 24)}   {per_month(price * 24 * 30)}", style=colors["ink-3"])
        if row.total(self.disk) is None:
            text.append("\nStorage billed separately.", style=colors["warning"])
        else:
            text.append(f"\nincludes {gb(self.disk)} disk", style=colors["ink-3"])

        text.append("\n\n")
        vram_value = (
            f"{total_vram(row.vram, row.count)} ({row.count}×{row.vram:g})"
            if row.count > 1
            else total_vram(row.vram, row.count)
        )
        kv_line(text, "gpus", str(row.count), width, colors, first=True)
        kv_line(text, "vram", vram_value, width, colors)
        kv_line(text, "per GB", per_hour(price / (row.vram * row.count), digits=4), width, colors)
        if row.reliability is not None:
            kv_line(text, "reliability", pct(row.reliability), width, colors)
        if row.disk_max is not None:
            kv_line(text, "max disk", gb(row.disk_max), width, colors)
        kv_line(text, "offer", row.id, width, colors, value_token="ink-2")

        prices = sorted(hourly(offer, self.disk) for offer in self.visible_rows)
        if len(prices) > 1:
            rank = prices.index(price) + 1
            text.append("\n\n")
            if rank == 1:
                text.append("Lowest", style=colors["accent"])
                text.append(f" of {len(prices)} offers", style=colors["ink-3"])
            else:
                text.append(f"+{per_hour(price - prices[0])}", style=colors["ink-2"])
                text.append(f" over lowest of {len(prices)}", style=colors["ink-3"])
        return text

    def offer_cta(self, colors, width: int) -> Text:
        left = hints(Text(no_wrap=True), [("enter", "rent")], colors, key_token="ink")
        note = "no charge yet"
        gap = max(width - left.cell_len - cell_len(note), 2)
        left.append(" " * gap)
        left.append(note, style=colors["ink-3"])
        return left

    def instance_card(self, row: Instance, colors, width: int) -> Text:
        mark, color = status_style(row.status, colors, frame=self.app.spin)
        text = Text()
        text.append(f"{row.name}\n", style=f"bold {colors['ink']}")
        text.append(f"{mark} {row.status}", style=color)
        text.append(f"   {gpu_label(row.gpu, row.count)}", style=colors["ink-3"])

        text.append("\n\n")
        text.append(usd(row.hourly), style=f"bold {colors['ink']}")
        text.append("/hr", style=colors["ink-2"])
        text.append(
            f"\n{per_day(row.hourly * 24)}   {per_month(row.hourly * 24 * 30)}", style=colors["ink-3"]
        )

        text.append("\n\n")
        address = f"{row.host}:{row.port}" if row.host else "assigning…"
        route = "direct ssh" if (row.host and row.direct) else "proxy ssh" if row.host else "pending"
        kv_line(text, "region", row.region, width, colors, first=True)
        kv_line(text, "address", address, width, colors)
        kv_line(text, "route", route, width, colors, value_token="ink-2")
        kv_line(text, "provider", provider_label(row.provider), width, colors, value_token="ink-2")
        kv_line(text, "id", row.id, width, colors, value_token="ink-2")
        return text

    def instance_cta(self, row: Instance, colors) -> Text:
        if row.status in {"stopped", "exited", "terminated"}:
            return hints(Text(no_wrap=True), [("d", "delete"), ("enter", "actions")], colors, key_token="ink")
        return hints(
            Text(no_wrap=True),
            [("c", "code"), ("x", "stop"), ("d", "delete")],
            colors,
            key_token="ink",
        )

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
        self.load_budget(fresh=True)

    def action_help(self):
        if len(self.screen_stack) == 1:
            self.push_screen(Help())

    def action_cursor(self, step: int):
        table = self.query_one(DataTable)
        if table.has_focus:
            table.move_cursor(row=max(table.cursor_row + step, 0))

    def action_table(self):
        search = self.query_one("#search", Input)
        if not search.has_focus and self.action_error:
            self.action_error = None
            self.paint_error()
            return
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

    def action_quick_action(self, action_name: str):
        if not self.rented or self.loading or self.load_error or not self.visible_rows:
            return
        if len(self.screen_stack) > 1 or not self.query_one(DataTable).has_focus:
            return
        row = self.visible_rows[self.query_one(DataTable).cursor_row]
        if not isinstance(row, Instance):
            return
        if action_name == "code" and row.status in {"stopped", "exited", "terminated"}:
            return
        if action_name == "stop" and row.status in {"stopped", "exited"}:
            return
        self.accept_result(InstanceAction(row, action_name))

    def action_select(self):
        if self.loading or self.load_error or not self.visible_rows or len(self.screen_stack) > 1:
            return
        row = self.visible_rows[self.query_one(DataTable).cursor_row]
        if isinstance(row, Offer):
            if not self.templates_loaded:
                self.load_templates()
            self.push_screen(Checkout(row, self.image, self.disk, self.templates), self.accept_result)
        else:
            self.push_screen(Actions(row), self.accept_result)

    def _cancel_connect(self, ident: str) -> None:
        cancel = self._connect_cancels.pop(ident, None)
        if cancel is not None:
            cancel.set()
        if self._connecting.pop(ident, None) is not None:
            self.paint_error()
            self._sync_status_spinner()

    def _update_connecting(self, ident: str, name: str, stage: str) -> None:
        if ident not in self._connecting:
            return
        self._connecting[ident] = (name, stage)
        self.paint_error()

    @work(group="connect-vscode")
    async def _connect_vscode(self, instance: Instance) -> None:
        ident, name = instance.id, instance.name
        cancel = threading.Event()
        self._connect_cancels[ident] = cancel
        initial = f"GPU is {instance.status}" if instance.status != "running" else "Connecting to SSH"
        self._connecting[ident] = (name, initial)
        self.paint_error()
        self._sync_status_spinner()
        if instance.status != "running":
            self.notify(f"{name} is {instance.status}. Will open VS Code when ready.", timeout=3)
        else:
            self.notify(f"Connecting to {name}. Will open VS Code when ready.", timeout=2)

        def on_progress(stage: str) -> None:
            if not cancel.is_set():
                try:
                    self.call_from_thread(self._update_connecting, ident, name, stage)
                except RuntimeError:
                    pass

        try:
            opened = await asyncio.to_thread(
                connections.connect_editor,
                self.provider,
                ident,
                progress=on_progress,
                cancel=cancel,
            )
            if opened and not cancel.is_set():
                self.notify(f"Opened {name} in VS Code", timeout=3)
                if self.rented:
                    self.refresh_instances_silently()
        except CrigError as failure:
            if not cancel.is_set():
                self.action_error = str(failure)
                self.notify(str(failure), title="Could not open VS Code", severity="error", timeout=6)
        finally:
            self._connect_cancels.pop(ident, None)
            self._connecting.pop(ident, None)
            self.paint_error()
            self._sync_status_spinner()

    def on_unmount(self) -> None:
        pending = list(self._connecting)
        for cancel in self._connect_cancels.values():
            cancel.set()
        self._connect_cancels.clear()
        self._connecting.clear()
        if self.handoff_connect is not None:
            for ident in pending:
                self.handoff_connect(ident)

    @contextmanager
    def _terminal_action(self) -> Iterator[None]:
        stack = ExitStack()
        try:
            try:
                stack.enter_context(self.suspend())
            except SuspendNotSupported:
                pass
            yield
        finally:
            # Textual resumes only when its suspend context exits without an exception.
            stack.close()

    def _start_async_code(self, instance: Instance) -> None:
        if self.demo:
            self.notify(f"Demo: code {instance.name}", timeout=3)
            return
        if instance.id in self._connecting:
            name, stage = self._connecting[instance.id]
            self.notify(f"{name}: {stage}. Will open VS Code when ready.", timeout=3)
            return
        if not connections.has_keypair(instance.id):
            try:
                with self._terminal_action():
                    _, pub = connections.ensure_keypair(instance.id)
                    self.provider.attach_ssh_key(instance.id, pub)
            except Cancelled:
                return
            except CrigError as failure:
                self.action_error = str(failure)
                self.paint_error()
                return
        self._connect_vscode(instance)

    def accept_result(self, result):
        if result is None:
            return
        if self.action_runner is None:
            self.exit(result)
            return
        self.action_error = None
        self.paint_error()
        if isinstance(result, InstanceAction) and result.action == "code":
            self._start_async_code(result.instance)
            return
        if isinstance(result, InstanceAction) and result.action in {"stop", "destroy"}:
            self._cancel_connect(result.instance.id)
        try:
            with self._terminal_action():
                selected_id, message = self.action_runner(result)
        except Cancelled:
            return
        except CrigError as failure:
            self.action_error = str(failure)
            self.paint_error()
            return
        if selected_id:
            self.selected_id = selected_id
        if message:
            self.notify(message, timeout=3)
        if not self.rented:
            self.action_view("instances")
        else:
            self.refresh_instances_silently()
        self.load_budget(fresh=True)
