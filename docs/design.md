# CLI experience

`crig` is the global ordinary-user client. It remembers a default provider, shows a searchable GPU
market and rented-instance list, and uses one restrained terminal interface: neutral grays with a
peach accent.

The interface is built from layered surfaces rather than boxes: background, surface (sidebar, status
bar, dialogs), and panel (inputs, quote). Focus is shown with a left accent bar. Market and Workspaces
are tabs (`1`, `2`). A sidebar inspects the selected row and collapses into a summary strip below
104 columns. Dialogs are keyboard lists with hotkeys. Delete has no single-key shortcut. `ctrl+p`
opens a command palette for views, sorting, refresh, and themes (`cloudrig`, `cloudrig-mint`,
`cloudrig-light`, and Textual's built-ins). A theme choice is saved to the ordinary config file,
except in demo mode. `?` lists every shortcut. While data loads, or when nothing matches, a block
wordmark splash fills the table area with the next useful keys. The sorted column is marked in the
header.

The main flow is search → review → fresh sudo → protected quote → rent → connect.
Selecting a result never spends money. `/` filters, `s` sorts, `r` refreshes, and Escape cancels.

Search and approved work stay unprivileged. The protected native component owns provider tokens,
private keys, allocation decisions, and connection grants. Client text and client confirmation are
not spending authority. A modified agent-facing client cannot turn a read operation into a rent.

After approval, ordinary native SSH, VS Code, rsync, and remote agents use a public key and a
purpose-limited signing socket. There are no personal-key imports, keychain fallbacks, or passwords
forwarded through an agent. The human handles privileged operations in a trusted terminal.
