# Security

When AI coding agents (Claude Code, Codex, etc.) run on your machine, they run as your user and can read any normal file you can read. Cloudrig protects your Vast.ai account with two simple mechanisms — no `sudo`, no background daemon, and no root installer.

## 1. Two scoped keys on Vast.ai

During `crig login`, you paste one Vast.ai API key and pick a short PIN. Cloudrig uses Vast.ai's API (`POST /api/v0/auth/apikeys/`) to mint two restricted keys, then discards the key you pasted:

| Key | Saved as | Permissions on Vast.ai |
| --- | --- | --- |
| `cloudrig-read` | `~/.config/cloudrig/read.key` (`0600`) | `misc`, `user_read`, `instance_read`, `billing_read` |
| `cloudrig-action` | `~/.config/cloudrig/action.key.enc` (`0600`) | `misc`, `user_read`, `instance_read`, `instance_write` |

- Neither saved key has `billing_write` or `user_write`, so Vast.ai's servers block adding credit, changing billing, or creating/deleting API keys.
- Agents can freely browse offers and inspect instances using the read-only key, without being able to spend money or delete GPUs.
- If you paste an already-restricted key that cannot mint sub-keys, Cloudrig encrypts that single key with your PIN instead of writing it in plaintext.

## 2. PIN-encrypted action key + `/dev/tty` confirmation

- The action key is encrypted at rest with `scrypt` + `AES-256-GCM` using your PIN. An agent reading `action.key.enc` only sees ciphertext.
- Renting, stopping, or destroying a GPU re-checks the live Vast.ai quote, prints the exact GPU/price or data-loss warning directly to `/dev/tty`, and prompts for your PIN (never `stdin`).
- The key is decrypted in memory for that single API call and never written to disk.

## 3. Per-instance SSH keys

Each rented GPU gets its own throwaway `ed25519` keypair in `~/.ssh/cloudrig/` with `ForwardAgent no` and `ForwardX11 no`. Destroying the GPU deletes its local SSH key, known-hosts entry, and SSH config block.

## Scope and honest limits

- **Local file deletion:** Because Cloudrig does not run as `root`, a same-user process can still delete `~/.config/cloudrig/action.key.enc`. Deleting that local file does not affect Vast.ai, spend money, or touch running GPUs — you simply run `crig login` again.
- **No automatic shutdown:** Closing the TUI or VS Code does not stop a GPU. Keep Vast.ai auto-billing off if you want a hard spend ceiling at your prepaid balance.
