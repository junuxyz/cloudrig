# Contributing

AI-assisted changes are welcome. Test the code you submit.

For bugs, include the command, expected and actual results, OS, and Cloudrig version.
Add the safe diagnostic from `crig status` if available. Remove account IDs and private paths.
Never post API keys, SSH keys, complete provider responses, or unreviewed startup logs.
Use GitHub's private vulnerability reporting for security issues when enabled.

Explain the user-visible problem and how you checked the fix. Keep changes focused.
Tests must not rent resources or change real accounts. Local provider fixtures do not prove live billing.

Read [AGENTS.md](AGENTS.md) and [architecture](docs/architecture.md). From the repository root:

```sh
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests scripts/preview.py
(cd admin && go test -race ./...)
(cd admin && go vet ./...)
```

## Run from source

Source builds require Go 1.26+. Install the command globally and run first-time setup:

```sh
uv tool install .
crig
```

`uv build` packages the compiled helper and its installer in a platform wheel.
After editing the helper, rebuild and reinstall the wheel, then run `crig` to update the service.
For direct helper development, `crig setup --source /path/to/cloudrig --service-only` builds and
installs from that checkout without replacing API keys. Review privileged installation in your
own terminal. See [distribution](docs/distribution.md) for release verification.
