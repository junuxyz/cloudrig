# Working on Cloudrig

Cloudrig is a minimal pure-Python CLI/TUI for renting Vast.ai GPUs, opening VS Code, stopping, and deleting them.

- Keep the codebase small, direct, and pure Python (no root daemon, no `sudo`, no multi-language build).
- Keep the security boundary simple and honest:
  - Read-only Vast.ai key (`read.key`) for browsing and instance reads.
  - Scoped no-billing action key (`action.key.enc`, encrypted with `scrypt` + `AES-256-GCM`) unlocked via `/dev/tty` PIN prompt only for mutations (`rent`, `stop`, `destroy`, `attach_ssh_key`).
- Keep visual tokens and display formats centralized in `src/cloudrig/style.py` (enforced by `tests/test_design_guards.py`).
- Run checks before finishing:

```sh
uv sync --group dev
uv run ruff check src tests scripts/preview.py
uv run pytest -q
```
