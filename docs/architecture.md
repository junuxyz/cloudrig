# Architecture

Cloudrig is a small Python package (`src/cloudrig/`) with one entry point, `crig`. It talks to the Vast.ai REST API over HTTPS and to remote GPUs over SSH. There is no background service and nothing runs as root.

## Modules

| Module | Responsibility |
| --- | --- |
| `cli.py` | Argument parsing, `login`/`logout`, plain and JSON output, and the bridge that runs paid actions for the dashboard. |
| `tui.py` | The Textual dashboard: offers and instances tables, inspector, checkout, actions, and help dialogs. |
| `vast.py` | Vast.ai client. Reads use the read-only key and short caches. Mutations unlock the action key with a PIN. |
| `vault.py` | Stores the read key and the PIN-encrypted action key in `~/.config/cloudrig/`. |
| `connections.py` | Per-instance SSH keys, the managed `~/.ssh/cloudrig/config`, SSH readiness probes, and opening VS Code. |
| `models.py` | Plain data types (`Offer`, `Instance`, `Rental`, `ImageTemplate`) and input validation. |
| `style.py` | Every color token, glyph, and display format. See the [design system](design-system.md). |
| `state.py`, `terminal.py` | Preferences, private file writes, progress output, and quiet subprocess calls. |
| `demo.py` | A fictional provider with the same interface as `VastClient`, used by `crig --demo`, tests, and `scripts/preview.py`. |

## How a paid action flows

1. The dashboard returns a `Rental` or `InstanceAction` to `cli.py` instead of calling Vast.ai itself.
2. The TUI suspends, and `vast.py` re-checks the live price or state, prints a confirmation card to `/dev/tty`, and asks for the PIN there.
3. The action key is decrypted in memory for that one request, then the TUI resumes in place.

Opening VS Code waits for the GPU to boot. When that takes a while, `crig` hands it to a detached `crig --connect-bg <id>` process so the dashboard stays usable. The security model is described in [security](security.md).

## Why Python

Cloudrig is pure Python and does not plan to add a compiled component.

An earlier version included a Go helper that ran as a root daemon to hold API and SSH keys. Go was chosen there for privilege isolation, not speed. Scoped Vast.ai keys and the PIN-encrypted action key replaced that helper, which left no reason for a second language.

Python's startup and import cost is about a tenth of a second. What users actually wait for is the Vast.ai API, around a second per request, and GPU boot, which takes tens of seconds to minutes. A compiled language would not make either faster, and moving off Python would mean replacing Textual, which the dashboard and design system are built on.

This is worth revisiting if Python's own time becomes a measurable part of what users wait for, or if Cloudrig needs to ship as a single binary that runs without Python installed.

## Tests and previews

Tests run against the demo provider and a local fake Vast.ai HTTP server, so they never touch a real account. `tests/test_design_guards.py` enforces that visual tokens and formats come from `style.py`. `scripts/preview.py` renders the SVG screenshots in `assets/` from the demo provider.
