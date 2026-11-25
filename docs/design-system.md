# Design system

Cloudrig has one visual surface: a Textual dashboard (`src/cloudrig/tui.py`) with plain Rich output from the
CLI (`cli.py`, `installation.py`, `onboarding.py`, `terminal.py`). This spec defines how both look so that
every screen reads as one product: quiet, monochrome, exact.

Status: adopted. Section 9 lists the audit findings this spec resolved, section 10 the migration that applied it.
`tests/test_style.py` pins the formats and contrast, and `tests/test_design_guards.py` enforces the rest.

## 1. Principles

1. **Ink, not color.** Hierarchy comes from four ink levels of the foreground. Hue is reserved for meaning.
2. **One accent, one job.** The accent marks *the thing to act on or prefer*: the selection marker, the
   sort direction, the lowest price, the focused primary button, the loading pulse. Nothing decorative.
3. **Space, not lines.** Group with blank lines and alignment. One hairline per screen at most (the
   inspector divider) plus the dialog outline.
4. **Say it once, the same way.** A price, a GPU count, a provider, or a key looks identical on every
   screen and in the CLI. Formatting goes through shared helpers, never inline f-strings.
5. **Money is never ambiguous.** Every amount carries its period. Every unknown is said in words.

## 2. Tokens

All tokens live in one module, `src/cloudrig/style.py`. `tui.py` CSS and Rich text both read from it. No other
file contains a hex color, an opacity percentage, or a glyph.

### 2.1 Base palette

| Token | Dark (`cloudrig`) | Light (`cloudrig-light`) | Use |
| --- | --- | --- | --- |
| `background` | `#0b0b0c` | `#fcfcfc` | Screen |
| `surface` | `#111113` | `#f6f6f7` | Dialogs, command palette |
| `panel` | `#18181b` | `#efeff1` | Toasts only |
| `foreground` | `#ededed` | `#18181b` | Ink source |
| `accent` | `#a5b4fc` | `#4f46e5` | See principle 2 |
| `success` | `#86efac` | `#15803d` | `running` status only |
| `warning` | `#fcd34d` | `#a16207` | Money caveats, transitional status, stale data |
| `error` | `#fca5a5` | `#b91c1c` | Failures and destructive actions |

`primary`, `secondary`, and `accent` in the Textual `Theme` are all set to `accent`. The unused
`secondary` blue (`#93c5fd` / `#2563eb`) is removed.

### 2.2 Ink levels

Ink is the foreground blended over the background. Ratios are per theme so that each level keeps its contrast
target in both themes (measured on `background`, WCAG):

| Level | Name | Dark ratio | Light ratio | Contrast (dark / light) | Use |
| --- | --- | --- | --- | --- | --- |
| 1 | `ink` | 1.00 | 1.00 | 16.8 / 17.3 | Values, names, titles, keys in hints |
| 2 | `ink-2` | 0.62 | 0.62 | 6.7 / 4.9 | Attributes, units, inactive labels, secondary buttons |
| 3 | `ink-3` | 0.38 | 0.50 | 3.2 / 3.4 | Labels, hint text, IDs, footnotes, placeholders |
| 4 | `ink-4` | 0.10 | 0.10 | n/a | Hairlines and dialog outline only, never text |

Rules:

- Text never goes below `ink-3`. The current 30% and 35% text (tabs, search glyph, placeholder, table header,
  group labels, `esc`) is 2.4 to 2.9:1 in dark and moves to `ink-3`.
- Light `ink-3` rises from 0.38 to 0.50. At 0.38 it measures 2.4:1 and is hard to read.
- CSS reads `$ink`, `$ink-2`, `$ink-3`, `$ink-4`, `$tint-1`, `$tint-2`, and `$scrim`. `style.css_variables`
  generates them for every theme, including Textual's built-in ones, as foreground alpha so they read the same
  on `background` and `surface`.

### 2.3 Fills

| Token | Value | Use |
| --- | --- | --- |
| `tint-1` | foreground 4% | Hover, input at rest, blurred table cursor |
| `tint-2` | foreground 8% | Focus: table cursor, input, secondary button, choice row |
| `scrim` | background 80% | Behind dialogs and the command palette |

The six ad hoc tints in use today (3, 4, 5, 6, 7, 8%) collapse to these two.

## 3. Typography

The terminal gives one face and one size, so hierarchy is ink plus a single weight.

- **Bold** is used for exactly: the wordmark, dialog titles, the headline price in the inspector and checkout,
  the focused table row, the focused choice, and the primary button. Nothing else is bold.
- No italic, underline (except hyperlinks in CLI output), reverse, or dim attributes. Dimness is an ink level.

### Case

| Kind | Case | Examples |
| --- | --- | --- |
| Labels, column headers, form labels, group labels, field names, key names | lowercase | `gpu`, `price/hr`, `disk`, `manage`, `enter` |
| Hint labels (status bar, help, splash) | lowercase verb phrase | `rent GPU`, `filter`, `instances`, `keys` |
| View names, dialog titles, buttons, action rows | Sentence case | `GPU offers`, `Rent RTX 5090`, `Rent GPU`, `Open in VS Code` |
| Messages, toasts, errors, footnotes | Sentence case with a period | `Billing starts when rented.` |
| Toast titles | Sentence case, no period | `Could not load` |

Proper names keep their case anywhere: GPU, VS Code, Vast.ai, RunPod, SSH. Today the status bar mixes
`enter Rent GPU` and `2 Instances` with `move` and `filter`; under this rule they read `enter rent GPU` and
`2 instances`.

## 4. Formatting

All formatting goes through helpers in `style.py`. The table below is the contract, and tests should pin it.

| Value | Helper | Format | Example |
| --- | --- | --- | --- |
| Hourly price | `per_hour(x)` | `$` + 3 decimals + `/hr` | `$0.426/hr` |
| Hourly price, storage unknown | `per_hour(x, partial=True)` | adds `+` | `$0.426+/hr` |
| Small rate | `per_hour(x, digits=4)` | 4 decimals | `$0.0133/hr` |
| Daily | `per_day(x)` | 2 decimals | `$10.21/day` |
| Monthly | `per_month(x)` | 0 decimals, thousands separator | `$1,306/mo` |
| Size | `gb(x)` | `:g` + space + `GB` | `32 GB` |
| Percent | `pct(x)` | 1 decimal | `99.8%` |
| GPU with count | `gpu_label(name, n)` | suffix `×N`, omitted when 1 | `RTX 5090 ×2`, `A100 SXM4` |
| Name with ID | `named(name, id)` | ID in parentheses after a space | `inference (demo-02)` |
| Provider | `PROVIDER_NAMES[id]` | display name, never the internal ID | `Vast.ai`, `RunPod` |
| Status | `status_mark(s)` | glyph + space + lowercase word | `● running` |

Headline prices split into two spans: the amount in `ink` bold and the unit in `ink-2`
(`$0.426` + `/hr`). In tables the column header carries the unit (`price/hr`) and cells show only the amount.

Not allowed: `/ hr`, ` /hr`, `per day`, `per month`, `1 × RTX 5090`, `×1`, `inference(demo-02)`, `vastai`
in visible text, `USD / hr`.

### Separators

- Inline metadata items are separated by **three spaces**: `Oregon, US   Vast.ai   84021`.
- Never `·`, `|`, `/`, or `-` as a separator. Commas only inside a single value (`Oregon, US`).
- Key hints: key in `ink`, one space, label in `ink-3`; three spaces between pairs.

## 5. Glyphs

| Glyph | Meaning | Color |
| --- | --- | --- |
| `●` | running | `success` |
| `○` | stopped, exited | `ink-3` |
| `◌` | any transitional status (starting, stopping, unknown) | `warning` |
| `›` | focused choice marker | `accent` |
| `↑` `↓` | sort direction in a column header | `accent` |
| `↑` `↓` | price moved since the last refresh | `ink-3` |
| `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | loading (10 frames, 80 ms) | `accent` |
| `×` | count multiplier | inherits |

- The spinner uses the same frames as Rich's `dots` spinner, so the TUI and CLI waits look the same. The
  current 4 frame, 250 ms pulse looks choppy, and its `⠦` frame doubles as the transitional status mark.
- Price change arrows drop red and green. A price moving is information, not a failure or a success.

## 6. Space and layout

All values are terminal cells.

### Horizontal

| Element | Value |
| --- | --- |
| Page gutter | 4 (2 when width < 104) |
| Gap between header items, tabs | 3 |
| Table to inspector | 3 margin + hairline + 3 padding |
| Inspector width | 42 |
| Dialog padding | 1 vertical, 3 horizontal |
| Dialog width | `76` for forms (checkout), `56` for menus and info (actions, help) |
| Field label column | 13 in the inspector, 8 in dialog forms |

Today the actions dialog is 60 wide. It becomes 56 to match help, so the two menu-style dialogs share a frame.

### Vertical rhythm

- Within a group: no blank line.
- Between groups: **1** blank line.
- Between sections (price block, facts block, call to action): **2** blank lines. Never 3.
- The status bar always has 1 blank row above it, including in compact layouts (height < 30). Today the
  compact inspector strip runs straight into the status bar.

### Lines

- One hairline style: `solid` at `ink-4`. Used for the inspector divider (left in wide, top in narrow).
- Dialogs and the command palette: `round` border at `ink-4`. No other borders, no double or heavy lines.

## 7. Components

### Header

`cloudrig` wordmark (`ink`, bold), then tabs (`ink` when active, `ink-3` at rest, `ink-2` on hover), then the
provider display name right-aligned in `ink-2`. Demo mode appends `demo` in `accent`.

### Filter bar

`/` glyph in `ink-3`, `accent` when focused. Placeholder `ink-3`. Right side meta: counts in `ink-2`, the money
figure in `ink` (`5 offers   from $0.426/hr`, `2 running   $1.582/hr`).

### Table

| Part | Style |
| --- | --- |
| Header | `ink-3`, lowercase; sorted column adds an accent arrow |
| Lead column (GPU or name) | `ink` |
| Attributes (vram, region, reliability, gpu on instances) | `ink-2` |
| IDs | `ink-3` |
| Best price | `accent` |
| Inactive row (stopped) | lead column `ink-2`, everything else `ink-3` |
| Cursor | `tint-2` + bold when focused; `tint-1`, no bold, when blurred |
| Hover | `tint-1` |

### Inspector

```
RTX 5090                      ink, bold
Oregon, US   Vast.ai          ink-3


$0.426/hr                     amount ink bold, unit ink-2
$10.21/day   $306/mo          ink-3
includes 40 GB disk           ink-3 (warning if storage is billed separately)


gpus         1                label ink-3, value ink
vram         32 GB
per GB vram  $0.0133/hr
...

Lowest of 5 offers            "Lowest" in accent, rest ink-3


enter rent GPU   no charge yet
```

The call to action is one line. If it does not fit, drop the trailing note rather than wrapping it.

### Dialog

```
Title                                        esc      bold ink / ink-3 (ink on hover)
meta   meta   meta                                    ink-3

body ...

Footnote sentence.                                    ink-3
note on the left                   Cancel  Rent GPU   ink-3 / buttons
```

- Every dialog uses this anatomy and the same subtitle line style.
- Footnotes are always `ink-3`. Today the footnote under the instance actions renders in full `ink`
  because it is created as a plain string.
- The note left of the buttons must fit on one line. `Your password is required to rent this GPU.` is cut
  off at width 76; shorten it to `Asks for your password.`, or move it to the footnote.

### Buttons

Two variants. At most one primary per dialog.

| Variant | Rest | Hover | Focus |
| --- | --- | --- | --- |
| Primary | `foreground` fill, `background` text, bold | `accent` fill | `accent` fill |
| Secondary | no fill, `ink-2` | `tint-1`, `ink` | `tint-2`, `ink` |

Destructive actions are never buttons in the dashboard. They are `-danger` choices (label in `error`) and are
confirmed by the `/dev/tty` PIN prompt.

### Choice rows

Marker column 2 cells (`› ` focused in `accent`, blank at rest). Label `ink-2` at rest, `ink` bold when focused,
`error` when destructive. Right side: permission tag (`pin`) in `ink-3`, then the hotkey in `ink-3` (`ink-2`
when focused). Group labels (`connect`, `manage`) in `ink-3`, 1 blank line above.

### Inputs

No border. `tint-1` at rest, `tint-2` focused, padding `0 1`. The label is `ink-3` and becomes `ink` while its
row has focus. A unit suffix (`GB`) is `ink-3`. A validation error is one line in `error` below the fields.

### Feedback

| Channel | Use | Style |
| --- | --- | --- |
| Toast | Transient confirmations (refreshed, update available) and load errors | `panel` fill, no border; error toasts get a left bar in `error` |
| Inline error row | An action failed and the user returned to the dashboard | one line in `error` under the filter bar until the next action |
| Splash | Loading, empty, and load error states for the whole list | centered; wordmark, tagline `ink-3`, state line, hints, version `ink-3` |

### Status bar

Hint pairs on the left, `? keys   ctrl+p commands` on the right. Width < 100 keeps the two most important pairs.

## 8. CLI output

The CLI cannot know the terminal background, so it does **not** use the hex palette. It uses a Rich `Theme`
defined in `style.py` with named styles mapped to the terminal's own ANSI colors:

| Style | Rich value | Use |
| --- | --- | --- |
| `crig.title` | `bold` | Step headings (`Protect your API keys from AI agents`) |
| `crig.muted` | `dim` | Hints, URLs explanation, footnotes |
| `crig.ok` | `green` | Completed paid or state-changing actions (`Rented inference (demo-02).`) |
| `crig.warn` | `yellow` | Charges continue, data may be stale |
| `crig.error` | `red` | `crig:` errors |

- Replace the hard-coded `#edb19e` errors (`cli.py:222`, `cli.py:228`) and the bare `green`
  (`cli.py:91`) with these styles.
- `print_rows` uses the same column names and helpers as the TUI table (`price/hr`, `RTX 5090 ×2`).
- URLs print as `underline link <url>`. Rich cannot combine a theme style with a per-URL link, so there is no
  `crig.link` style.
- `Prompt` and `Confirm` get `dim` choices and defaults instead of Rich's default magenta and cyan.
- The provider menu in onboarding uses the same key-hint idea: `1  Vast.ai`, `2  RunPod`, `0  Cancel`.
- The progress spinner is Rich `dots` with the description in default ink and elapsed time in `crig.muted`.

## 9. Audit (what this spec fixes)

Measured against the dashboard rendered at 120×36 and 80×24 in both themes, with demo data.

**Color**

- Seven opacity values for text (30, 35, 38, 40, 55, 62, 70%) and six for fills (3 to 8%), split between CSS
  (`tui.py:404-498`) and `palette()` (`tui.py:92`). Three of the text levels fail 3:1.
- Light theme `faint` is 2.4:1.
- `secondary` is defined and never used.
- Red and green on price changes compete with error and running.
- The CLI has its own error color `#edb19e`, which matches neither theme.

**Formatting**

- Hourly rate appears as `$0.426 / hr`, `$0.0133 /hr`, `$1.582/hr`, `$1.156/hr`, `/hr` (compact), and
  `USD / hr` (CLI).
- Day and month appear as `per day` and `per month`, plus `/day` in compact.
- GPU count appears as `RTX 5090 ×2`, `A100 SXM4 ×1`, and `1 × RTX 5090`.
- Provider appears as `vastai` in the dashboard and `Vast.ai` in the CLI.
- `inference(demo-02)` has no space in CLI messages.

**Case**

- The status bar mixes `Rent GPU` and `Instances` with lowercase hints. Help mixes `GPU offers, Instances`
  with lowercase descriptions.

**Layout**

- Actions dialog 60 wide, help 56, checkout 76. Two menu-style dialogs on different frames.
- Blank line runs of 1, 2, and 3 in the inspector and splash.
- Compact layout: inspector summary touches the status bar.
- The inspector call to action wraps (`nothing is charged` / `yet`).

**Bugs found while rendering**

- Instance actions footnote renders in full ink (`tui.py:330`, plain string in a `Static`).
- Checkout note truncates to `Your password is required to rent this` (`tui.py:196`, `#next-step` height 1).
- Rich text styles are baked at paint time. An open dialog keeps the old theme's ink after a theme change, so
  in light mode the checkout headline price turns near-invisible.
- `assets/*.svg` screenshots predate the current copy and colors (for example, they still show
  `Sudo will check the price before rental.`).

## 10. Migration plan

Each step is a small, reviewable commit. Steps 1 to 3 change no visible behavior beyond the fixes listed.

1. **Tokens.** Add `style.py` with the palette, per-theme ink ratios, fills, glyphs, spinner frames, and
   `PROVIDER_NAMES`. Build both `Theme` objects and the CSS color constants from it. Move `palette()` there and
   return `ink`, `ink-2`, `ink-3`, `accent`, `success`, `warning`, `error`.
2. **Formatters.** Add the section 4 helpers with tests that pin each example string. Replace inline
   f-strings in `tui.py` and `cli.py`.
3. **Ink sweep.** Replace every opacity in the CSS with the two fills and the three ink levels. Repaint Rich
   text on theme change for any screen on the stack, not only the base screen.
4. **Components.** Apply section 7: dialog widths, footnote style, one-line CTA, button variants, glyphs,
   spinner, case rules in hints and help, vertical rhythm, compact gap.
5. **CLI.** Add the Rich theme from section 8 to `terminal.console` and migrate `cli.py`, `installation.py`,
   and `onboarding.py`.
6. **Screenshots.** Run `uv run python scripts/preview.py` and commit the regenerated `assets/*.svg`. Add a
   light-theme checkout to the preview set.
7. **Guards.** Add tests that fail when a hex color or a `%` opacity appears outside `style.py`, when `·`
   appears in any user-visible string, and when a dashboard screenshot at 80×24 has a line that overflows.

## 11. Review checklist

Use before merging any UI change:

- [ ] No new hex, opacity, or glyph outside `style.py`.
- [ ] Every price, size, count, and provider goes through a helper.
- [ ] Hue only for accent, running, caveat, or failure.
- [ ] Bold only where section 3 allows it.
- [ ] Case follows the section 3 table.
- [ ] Blank line runs are 1 or 2.
- [ ] Checked at 120×36 and 80×24, dark and light.
- [ ] `assets/*.svg` regenerated.
