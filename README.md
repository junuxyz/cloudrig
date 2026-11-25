# Cloudrig

Run cloud GPU experiments from your terminal — fast, simple, and agent-friendly.

![Cloudrig dashboard with demo data](assets/instances.svg)

## Supported Features

- **GPU search**: browse, filter, and sort live Vast.ai offers in the TUI or CLI (`--plain` / `--json`).
- **One-command VS Code**: injects a throwaway SSH key at boot, tries direct SSH before proxy, and cleans up on destroy.
- **Agent-safe controls**: auto-mints a read-only key and a PIN-locked, no-billing action key. Rent, stop, and destroy confirm on `/dev/tty` with live price checks.

## Install

```sh
uv tool install git+https://github.com/junuxyz/cloudrig
crig
```

Try `crig --demo` without an account. Currently supports macOS and Linux.

## Commands

```sh
crig                  # Open the dashboard (runs login on first use)
crig --gpu [QUERY]    # Browse GPU offers (e.g. crig --gpu 4090)
crig --rented         # List your rented GPUs
crig NAME code        # Open VS Code on a running GPU
crig NAME stop        # Stop a GPU (PIN required)
crig NAME destroy     # Delete a GPU (PIN required)
crig login            # Connect Vast.ai and set your PIN
crig logout           # Remove saved local keys
```

Add `--plain` or `--json` for script-friendly output.

## Security

Cloudrig splits your Vast.ai access into two scoped keys automatically during `crig login`:

- **Read key** (`read.key`) — browse offers and check instances with zero prompts. Cannot rent, stop, destroy, or touch billing.
- **Action key** (`action.key.enc`) — rent, stop, and destroy only (no billing or API-key permissions), encrypted with a PIN read directly from `/dev/tty`.

The key you paste during `crig login` is never saved. See [docs/security.md](docs/security.md).

## License

[MIT license](LICENSE)
