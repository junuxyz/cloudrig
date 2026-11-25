# Contributing

## Development setup

Requires Python 3.11+ and `uv`.

```sh
uv sync --group dev
uv run ruff check src tests scripts/preview.py
uv run pytest -q
```

Run the TUI locally with fictional data:

```sh
uv run crig --demo
```
