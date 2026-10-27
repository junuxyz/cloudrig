# Working on Cloudrig

Read [docs/architecture.md](docs/architecture.md) before changing responsibilities or persistence.

- Keep the CLI, workspace workflow, and privileged helper responsibilities explicit.
- Prefer direct functions and small data types. Add an abstraction when existing callers need it.
- Delete unused paths and parameters after migrating callers. Avoid compatibility wrappers for internal APIs.
- Validate configuration, provider responses, and privileged requests at their boundaries. Trust validated
  immutable values inside the application instead of repeating the same checks.
- Keep defaults in one place. Platform paths and provider contracts are explicit constants; project choices
  belong in configuration. Do not silently substitute another provider, instance, or price.
- Every guard should address a concrete failure. Keep protections for paid requests, credentials, and file
  transfers. Avoid speculative fallbacks, blanket retries, and checks for impossible internal states.
- Preserve instance receipts on ambiguous paid requests. Never turn a setup failure into another rental.
- Test behavior through commands and real tool boundaries where practical. Test doubles must not replace
  the policy being tested. Do not add tests that merely repeat the implementation.
- Leave unrelated working-tree edits intact. Use descriptive branches without a `codex/` prefix.

Run `uv run pytest -q`, `uv run ruff check src tests`, and `go test -race ./...` plus `go vet ./...`
from `admin/` for changes spanning both executables. See the architecture document for build commands.
