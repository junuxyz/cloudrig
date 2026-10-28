# Terminal interface

`crig` opens the dashboard. First use runs account setup before opening it.
Project review uses a full-screen browser, with `--numbered` as an alternative.

## Controls

| Key | Action |
| --- | --- |
| `1`, `2` | Switch between offers and instances. |
| Arrows or `j`/`k` | Move between rows. |
| Enter | Review a rental or choose an instance action. |
| `/`, `s`, `r` | Filter, sort offers, or refresh. |
| `g` | View setup progress. |
| Escape | Close a dialog or clear the filter. |
| `?`, `ctrl+p` | Open keyboard help or commands and themes. |

Delete has no single-key shortcut. Checkout validates name, disk, and image before returning a
rental. Sudo authentication happens outside the UI; the helper then runs without another prompt.
Actions return to the dashboard afterward.
Provider errors stay visible, and stale prices cannot start checkout.

## Progress and layout

Setup runs in the background. Its current step and elapsed time appear above the table.
View setup or `g` opens recorded events and the log location. Done or Escape returns to browsing.
Ready and failed states stay visible. Closing the dashboard does not cancel setup.
Progress describes saved setup checks, not continuous GPU health or a predicted finish time.

The detail pane shows the selected instance or offer. Below 104 columns it becomes a summary
under the table. Themes persist outside demo mode. Project review shows requirements, complete
compute-plus-disk quotes, upload exclusions, and the dependency command.
