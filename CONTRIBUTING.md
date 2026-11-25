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

## Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) with the type list from [Angular's commit guidelines](https://github.com/angular/angular/blob/main/contributing-docs/commit-message-guidelines.md).

```text
<type>(<optional scope>): <summary>
```

| Type       | Use for                                                        |
| ---------- | -------------------------------------------------------------- |
| `feat`     | A new user-facing feature                                      |
| `fix`      | A bug fix                                                      |
| `docs`     | Documentation only (README, `docs/`, docstrings)               |
| `style`    | Formatting with no change in behavior (whitespace, ruff format) |
| `refactor` | A code change that neither fixes a bug nor adds a feature      |
| `perf`     | A change that makes something faster or lighter                |
| `test`     | Adding or fixing tests only                                    |
| `build`    | Packaging and dependencies (`pyproject.toml`, `uv.lock`)       |
| `ci`       | GitHub Actions workflows in `.github/`                         |
| `chore`    | Upkeep that fits nothing above (version bumps, `.gitignore`)   |
| `revert`   | Reverting an earlier commit                                    |

- **Summary:** imperative mood, lowercase, no trailing period, at most 72 characters (`fix: show new rentals as loading`, not `Fixed the status bug.`).
- **Scope:** optional, names the area touched: `tui`, `cli`, `vast`, `vault`, `connections`, `style`.
- **One change per commit.** If the summary needs "and", split the commit. Tests and docs for a change ride in the same commit as the change.
- **One line only.** No body or footer. Longer context goes in the pull request description.
- **Breaking changes:** add `!` after the type (`feat(cli)!: rename --rented to --instances`) and describe the migration in the pull request.
- **Pull request titles** follow the same format, so a squash merge produces a valid commit.
