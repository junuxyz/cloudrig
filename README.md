# Cloudrig

**Run cloud GPU experiments from your terminal — fast, simple, and agent-friendly.**

Run `crig` to open the interactive TUI and browse GPU offers or manage your rented GPUs.

<p align="center">
  <img src="assets/market.svg" alt="Cloudrig GPU offers with demo data" width="720">
</p>

<p align="center">
  <img src="assets/instances.svg" alt="Cloudrig dashboard with demo data" width="720">
</p>

## What you can do

- **Find a GPU**: Browse live Vast.ai offers and sort them by price, memory, or region.
- **Open VS Code on your GPU**: Select a rental and press `c`. Setup is asynchronous, so you can keep working until VS Code opens automatically. [How warm start works](docs/architecture.md#vs-code-warm-start)
- **Control rentals with your PIN**: Agents can look up offers and check your rentals. For more sensitive actions like renting, stopping, or deleting a GPU, you enter your PIN. [Security details](docs/security.md)

## Install

```sh
uv tool install git+https://github.com/junuxyz/cloudrig
crig
```

(I will make official PyPI distribution when ready)

One time setup to connect it with your Vast AI account:

```bash
crig login            # Connect Vast.ai and set your PIN
```

Try `crig --demo` without an account. Currently supports macOS and Linux.

## Commands

```sh
crig                  # Open the dashboard (runs login on first use)
crig login            # Connect Vast.ai and set your PIN
crig logout           # Remove saved local keys
crig --gpu [QUERY]    # Browse GPU offers (e.g. crig --gpu 4090)
crig --rented         # List your rented GPUs
crig NAME code        # Open VS Code on a running GPU
crig NAME stop        # Stop a GPU (PIN required)
crig NAME destroy     # Delete a GPU (PIN required)
```

Add `--plain` or `--json` for script-friendly output. For example, `crig --rented --json` lists rented GPUs as JSON.

## License

[MIT license](LICENSE)
