# Cloudrig Design System

Cloudrig shares one visual and interaction language across its Textual TUI, Rich CLI output, and `/dev/tty` confirmation prompts.

## Principles

1. **Whitespace over chrome.** Separate regions with open space and subtle tonal shifts rather than borders, divider rules, pill badges, or box-drawing frames.
2. **Mostly monochrome.** Neutral surfaces and text carry the interface. Color is reserved for live meaning: lowest price, active sort, instance state, warnings, and errors.
3. **Tabular alignment.** Hardware specs and prices are scanned vertically — right-align numeric columns and format units consistently everywhere.
4. **In-place continuity.** Tab switches, refreshes, boot polling, and `/dev/tty` PIN prompts should not flash splash screens or cold-restart the TUI.

### Avoid

- Borders or box-drawing frames (`border: round`, `┌`, `│`, `╭`) on tinted surfaces.
- Vertical left accent stripes (`▎`, `┃`, `border-left: thick`) on rows, tabs, inputs, or modals.
- Middle dots (`·`) or pipes (`|`) as inline separators; use whitespace instead.
- Pill or badge backgrounds around tabs, counts, or mode tags (such as `demo`).
- Full-width horizontal divider lines slicing the header, inspector, or footer.
- Emojis, decorative icons, or Rich `dim` / `italic` / `reverse` in the TUI.
- Red styling on unfocused destructive actions (`Delete instance` stays neutral at rest and turns `$error` only when focused).

## Surfaces & Color

All color tokens, themes, and glyphs live in `src/cloudrig/style.py` (enforced by `tests/test_design_guards.py`). No other module should hardcode hex colors, CSS opacity percentages, or UI glyphs.

### Surface & Ink Tokens

| Token | Role |
| --- | --- |
| `canvas` | Base screen background (header, table, footer) |
| `surface` | Secondary tonal regions (filter strip, side inspector, checkout quote block) |
| `elevated` | Floating overlays (dialogs, command palette, toasts) |
| `hover` | Hover state and unfocused table selection |
| `active` | Focused table row, focused input, focused control |
| `scrim` | Translucent `canvas` backdrop behind modals |
| `border-subtle` / `border` | Hairline separators (compact layout, `/dev/tty` cards) and scrollbar thumb |
| `ink` / `ink-2` / `ink-3` | Primary, secondary, and muted text hierarchy |

### Semantic Hue Usage

| Token | Use for | Do not use for |
| --- | --- | --- |
| `accent` | Lowest offer price, active sort arrow, focused filter `/`, focused primary button, focused choice `›`, command match highlight, `demo` tag | Borders, left bars, tab pills, row fills, headings |
| `success` | Running instance (`●`), CLI confirmation | Price drops, generic badges |
| `warning` | Booting/loading spinner, low balance or runway, billing caveats | Focus rings, routine metadata |
| `error` | Failed state (`✕`), `$0` balance, error banners, focused destructive action | Unfocused menu items |

### Themes & Contrast

Cloudrig ships four built-in themes in `src/cloudrig/style.py`, switchable from `ctrl+p`:

- `cloudrig` — default neutral dark
- `cloudrig-light` — light paper neutral
- `cloudrig-warm` — warm dark neutral
- `cloudrig-ansi` — inherits the terminal's own background and ANSI colors

Other Textual themes derive surface and ink steps automatically via `style.css_variables()`. Every built-in theme must maintain readable contrast across `canvas`, `surface`, and `elevated` (WCAG AA `≥ 4.5:1` for `ink`, `ink-2`, and semantic colors; `≥ 3:1` for muted `ink-3`), verified in `tests/test_style.py`.

## Typography, Glyphs & Formatting

- **Weight:** Use the three ink levels (`ink`, `ink-2`, `ink-3`) for hierarchy. Reserve **bold** for structural anchors: the brand wordmark, active tab, focused table or menu row, dialog/inspector titles, headline hourly price, and primary CTA button.
- **Casing:** Use lowercase for the wordmark (`cloudrig`), table headers, field labels, group labels, and keybind hints. Use sentence case for tabs, dialog titles, buttons/actions, and prose (with trailing periods on full-sentence notes and errors).
- **Glyphs:** Defined as constants in `src/cloudrig/style.py`:
  - Status: `●` running (`success`), `○` stopped (`ink-3`), `✕` failed/error (`error`), Braille spinner (`⠋…⠏`) for booting or loading.
  - Controls: `↑` / `↓` sort direction, `/` filter prefix, `›` focused action item, `─` hairline rule (`/dev/tty` cards and compact summary divider only).
- **Value formatting:** Route all numbers and labels through `src/cloudrig/style.py`:
  - Prices: 3 decimals hourly (`$0.426/hr`, `+` suffix when storage is excluded), 2 decimals daily (`$10.21/day`) and for balances, whole dollars monthly (`$306/mo`).
  - Hardware & reliability: `GB` with a space (`32 GB`), 1-decimal percentages (`99.8%`), multi-GPU counts with `×` (`RTX 5090 ×2`, omit `×1`), and total VRAM across all GPUs in both table and inspector (with per-GPU breakdown in the inspector when count > 1).
  - Runway & names: compact duration in the header (`~11h 38m`, `>60d`, `0m`), full duration with local ETA in detailed/CLI views, and `name (id)` for labeled instances.

## Layout & Components

### Responsive Structure

- **Wide:** Split view with a full-width data table on the left and a borderless `$surface` inspector panel on the right.
- **Compact:** Full-width table with a short bottom summary strip instead of the side inspector; ID/offer columns move into the table.
- **Short:** Tighten outer margins and vertical gaps so data rows keep most of the viewport.

### Workspace Shell

- **Header:** Open on `$canvas` with no bottom divider. Wordmark and navigation tabs (`GPU offers`, `Instances` with running count) sit left; balance, compact runway, and optional `demo` tag sit right. Active tab is bold `$ink`, inactive tabs are `$ink-3`.
- **Filter strip:** Single borderless `$surface` row (`$active` on focus) spanning the table width, with `/` + text input on the left and live summary or inline loading spinner on the right.
- **Data table:** Open on `$canvas` with dynamic column widths that fill the available space and right-aligned numeric columns. Focused selection uses full-width `$active` fill + bold text (softer `$hover` when blurred). Stopped instances stay subdued so active ones stand out.
- **Inspector:** Borderless `$surface` panel separated into identity, pricing, right-aligned key/value specs, and pinned bottom action hints using blank rows rather than divider lines.
- **Footer:** Open on `$canvas`, context-aware to the focused view or widget, with keys in `$ink-2` and descriptions in `$ink-3` so it stays quieter than table data. Background polling shows an inline syncing indicator on the right.

### Overlays & States

- **Dialogs & palette:** Borderless `$elevated` cards over `$scrim`.
  - `Checkout` pairs clean label/input rows (`$surface` → `$active` on focus) with an inset `$surface` cost breakdown. `Cancel` stays neutral; `Rent GPU` uses high-contrast monochrome at rest (`$ink` fill, `$canvas` text) and shifts to `$accent` on focus/hover.
  - `Actions` groups choices (`connect`, `manage`), marks the focused row with `›`, and keeps `Delete instance` neutral until focused.
  - `CommandPalette` (`ctrl+p`) shares the same borderless `$elevated` styling and exposes navigation, sorting, refresh, help, and theme switching.
- **Loading, empty, and error states:**
  - Show a centered loading message only on initial empty load; subsequent refreshes and polls use inline spinners while keeping existing rows visible.
  - Empty states explain why nothing matched and show the recovery key (`esc` to clear filter or `1` to browse GPUs).
  - Errors appear in a dismissible inline `$surface` strip above the table; transient confirmations use auto-dismissing `$elevated` toasts.

## Keyboard, CLI & `/dev/tty`

| Scope | Key | Action |
| --- | --- | --- |
| Global | `1` / `2` | Switch between `GPU offers` and `Instances` |
| Global | `/` | Focus filter |
| Global | `r` | Refresh rows and balance |
| Global | `?` | Keyboard help |
| Global | `ctrl+p` | Command palette & theme switcher |
| Global | `q` | Quit |
| Table | `↑` `↓` / `j` `k` | Move selection |
| Offers | `s` | Cycle sort (`price`, `vram`, `region`, `gpus`) |
| Table | `enter` | Open `Checkout` (Offers) or `Actions` (Instances) |
| Instances | `c` / `x` | Direct VS Code / Stop (no hotkey for Delete) |
| Table / Filter | `esc` | Clear filter, dismiss error, or return to table |

- **TUI handoff:** Interactive actions that require a `/dev/tty` PIN prompt or launch VS Code suspend the live TUI and resume in place with a refresh and toast, rather than exiting and restarting the app.
- **CLI & `/dev/tty` parity:** `crig --plain`, CLI progress spinners, and `/dev/tty` confirmation cards reuse the same formatters, glyphs, and column conventions from `src/cloudrig/style.py`, and degrade cleanly when `NO_COLOR` is set.
