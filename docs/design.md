# Terminal interface

`crig` opens Instances. First use guides account setup. Press `1` for GPU offers and `2` for Instances.

| Key | Action |
| --- | --- |
| Arrows or `j`/`k` | Move between rows. |
| Enter | Review a rental or open instance actions. |
| `c`, `x` | In the instance menu, open VS Code or stop. |
| `/`, `s`, `r` | Filter, sort offers, or refresh. |
| Escape, `?`, `ctrl+p` | Back, keyboard help, commands and themes. |

Instance actions are Open in VS Code, Stop, and Delete. Delete has no single-key shortcut.
Stopping and deleting require sudo. Rental checkout validates its fields before requesting authentication.
Provider errors remain visible after returning to the dashboard. Stale offer data cannot start checkout.

The detail pane shows the selected GPU and quote. Narrow terminals show a compact summary.
Themes persist outside demo mode. Demo mode never contacts a provider or writes preferences.
Instances refresh every ten seconds while visible. There are no project controls or background setup panels.
