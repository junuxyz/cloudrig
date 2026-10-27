"""A short review before the existing protected rental flow."""

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Static

from cloudrig.models import Offer
from cloudrig.project import Project
from cloudrig.tui import THEMES, palette


class WorkspaceApp(App[Offer | None]):
    TITLE = "cloudrig · project workspace"
    CSS = """
    Screen { background: $background; color: $foreground; }
    #page { padding: 1 3; }
    #brand { height: 1; color: $primary; text-style: bold; }
    #title { height: 2; text-style: bold; }
    #subtitle { height: auto; margin-bottom: 1; color: $foreground-muted; }
    #body { height: auto; min-height: 8; }
    #offers { width: 2fr; height: auto; max-height: 12; background: $surface; }
    #summary { width: 1fr; height: auto; margin-left: 2; padding: 1 2; background: $surface; }
    #details { height: auto; margin-top: 1; padding: 1 2; background: $surface; }
    #note { height: auto; color: $foreground-muted; margin-top: 1; }
    #actions { height: 3; margin-top: 1; }
    Button { margin-right: 2; border: none; }
    #launch { background: $primary; color: $background; min-width: 29; }
    #cancel { background: $panel; color: $foreground; }
    #footer { height: auto; color: $foreground-muted; margin-top: 1; }
    .compact #body { layout: vertical; }
    .compact #offers { width: 1fr; }
    .compact #summary { width: 1fr; margin: 1 0 0 0; padding: 0 1; }
    """
    BINDINGS = [
        Binding("q,escape", "quit", "Back"),
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
    ]

    def __init__(self, project: Project, offers: list[Offer], *, demo=False, setup=False):
        super().__init__()
        self.project, self.offers, self.demo, self.setup = project, offers, demo, setup
        self.selected = 0

    def compose(self) -> ComposeResult:
        p = self.project
        with VerticalScroll(id="page"):
            yield Static(
                "▲  cloudrig   /   project workspace" + ("   /   DEMO" if self.demo else ""), id="brand"
            )
            yield Static(Text(f"Open {p.name} on a GPU.", style="bold", overflow="fold"), id="title")
            yield Static(
                Text(
                    f"{p.vram:g}+ GB / GPU  ·  {p.count} GPU  ·  up to ${p.max_hourly:g}/hr"
                    f"  ·  {p.disk} GB disk"
                ),
                id="subtitle",
            )
            with Horizontal(id="body"):
                yield DataTable(id="offers", cursor_type="row")
                yield Static(id="summary")
            yield Static(id="details")
            yield Static(
                "The hour estimate is for planning. It does not stop billing.\n"
                "Closing VS Code leaves the GPU rented. Storage and transfer charges may remain.",
                id="note",
            )
            with Horizontal(id="actions"):
                yield Button("Try this workspace" if self.demo else "Review quote & rent", id="launch")
                yield Button("Back", id="cancel")
            yield Static(
                "DEMO · fictional offers · no rental, upload, or editor launch"
                if self.demo
                else "↑ ↓ choose   ·   Enter review   ·   Esc back   ·   rental needs fresh sudo",
                id="footer",
            )

    def on_mount(self):
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = "cloudrig"
        table = self.query_one(DataTable)
        table.add_columns("GPU", "GB / GPU", "LOCATION", "USD / HR")
        for row in self.offers:
            table.add_row(row.title, f"{row.vram:g}", row.region, f"${row.total(self.project.disk):.3f}")
        self.query_one("#launch", Button).disabled = not self.offers
        self.update_details()
        table.focus()

    def on_resize(self, event):
        self.set_class(event.size.width < 100, "compact")
        if self.is_mounted and self.query("#details"):
            self.update_details()

    def action_move(self, delta: int):
        table = self.query_one(DataTable)
        table.move_cursor(row=max(0, min(len(self.offers) - 1, table.cursor_row + delta)))

    @on(DataTable.RowHighlighted)
    def highlighted(self, event):
        self.selected = event.cursor_row
        self.update_details()

    def update_details(self):
        p = self.project
        colors = palette(self)
        if self.offers:
            row = self.offers[self.selected]
            total = row.total(p.disk)
            compact = self.size.width < 100
            summary = Text("SESSION ESTIMATE  " if compact else "SESSION ESTIMATE\n\n", style=colors["muted"])
            summary.append(f"${total * p.hours:.2f}", style=f"bold {colors['primary']}")
            summary.append(f" / {p.hours:g} hours" + ("  ·  " if compact else "\n\n"))
            summary.append(f"${total:.3f}/hr with quoted disk")
            if not compact:
                summary.append("\nLowest quoted price" if self.selected == 0 else "\nYour selection")
            self.query_one("#summary", Static).update(summary)
        else:
            self.query_one("#summary", Static).update(
                "No complete quotes match.\nRaise --max-hourly or change --vram."
            )
        grid = Table.grid(padding=(0, 3))
        grid.add_column(style=colors["muted"])
        grid.add_column()
        if self.size.width >= 100:
            grid.add_row("Project", Text(str(p.root)))
        grid.add_row("Remote folder", Text(p.remote))
        grid.add_row("First connection", "Upload project → check CUDA → open VS Code")
        grid.add_row("Next connection", "Reuse GPU and remote files. No automatic upload.")
        grid.add_row(
            "Dependencies",
            Text(
                " ".join(p.setup)
                if self.setup and p.setup
                else "Image packages. Use --setup for project dependencies."
            ),
        )
        if self.size.width >= 100:
            grid.add_row("Image", Text(p.image))
            grid.add_row("Upload exclusions", ".env*, private keys, cloud credentials, .git, .venv, caches")
        self.query_one("#details", Static).update(grid)

    @on(DataTable.RowSelected)
    def row_selected(self):
        self.query_one("#launch", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.id == "launch" and self.offers:
            self.exit(self.offers[self.selected])
        elif event.button.id == "cancel":
            self.exit(None)
