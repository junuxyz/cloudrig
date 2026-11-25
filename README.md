# Cloudrig

Rent a Vast.ai GPU, open VS Code, stop it, and delete it from your terminal.

![Cloudrig dashboard with demo data](docs/instances.svg)

## Install

```sh
uv tool install cloudrig
crig
```

Try `crig --demo` without an account. Supports macOS and Linux.

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

[MIT license](LICENSE)
