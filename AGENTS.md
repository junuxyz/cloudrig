# Working on Cloudrig

For user onboarding, follow [docs/agents.md](docs/agents.md).

Read [docs/architecture.md](docs/architecture.md) before changing responsibilities or saved state.

- Keep CLI, project workflow, and root-helper responsibilities explicit.
- Prefer direct functions and small data types. Add abstractions when implemented callers need them.
- Migrate callers, then delete unused paths and parameters. Do not keep wrappers for old internal APIs.
- Validate configuration, provider responses, and privileged requests where they enter the application.
  Trust validated immutable values inside the application instead of checking them again.
- Keep defaults in one place. Use constants for platform paths and provider contracts, and configuration
  for project choices. Never silently substitute another provider, instance, or price.
- Tie each guard to a concrete failure. Keep checks for paid requests, credentials, and transfers.
  Avoid speculative fallbacks, blanket retries, and guards for impossible internal states.
- Preserve receipts when a paid request's outcome is unknown. A setup failure must never rent another GPU.
- Test behavior through commands and actual tool calls where practical. A test double must not replace
  the policy under test. Do not add tests that repeat the implementation without checking behavior.
- Preserve unrelated working-tree edits. Use descriptive branch names without a `codex/` prefix.

For changes to both executables, run `uv run pytest -q`, `uv run ruff check src tests`, and, from
`admin/`, `go test -race ./...` and `go vet ./...`. Build commands are in [docs/verification.md](docs/verification.md).
