# Architecture

The Python CLI manages projects, the dashboard, transfers, and editor commands as the local user.
The installed Go helper holds credentials and SSH keys as root. Cloudrig routes new spending
and SSH approvals through the helper.

## Python modules

| Module | Purpose |
| --- | --- |
| `cli.py`, `workspace_cli.py` | Parse commands, review actions, and run them. |
| `onboarding.py` | Guide installation and account setup. |
| `installation.py` | Verify bundled files and signed releases; coordinate installation, updates, and removal. |
| `updates.py` | Check official GitHub releases and limit dashboard notices to one check every 10 days. |
| `tui.py`, `workspace_tui.py` | Display offers, instances, and progress; return selections. |
| `workspace.py` | Rent or resume an instance, upload a project, and check readiness. |
| `preparation.py` | Run background setup and save progress. |
| `project.py` | Load settings, select offers, lock projects, and save bindings. |
| `broker.py` | Call the helper and define provider defaults. |
| `connections.py` | Build and run SSH, rsync, startup, and editor commands. |
| `state.py` | Replace local state files atomically under a write lock. |
| `models.py` | Validate offers, instances, and rentals. |
| `security.py` | Check local paths, filter child environments, and redact diagnostics. |
| `terminal.py`, `demo.py` | Handle terminal prompts and fictional demo data. |

`crig` opens the dashboard, or setup when no provider is configured. Project review uses the
full-screen browser; `--numbered` selects prompts. Non-interactive commands print results.
Setup saves the provider preference only after login succeeds. Demo mode never reads preferences
or writes configuration.

Settings apply in order: defaults, global provider, `crig.toml`, then command options.
An existing binding keeps its provider unless the command specifies `--provider`.
Command modules own prompts. The workspace code takes options without importing a parser or UI.

## Project state and preparation

The CLI records a submission before a paid request and its returned ID before waiting for SSH.
Only the protected journal can establish that a failed request was never submitted.
An uncertain response or missing instance never causes an automatic replacement rental.

| Binding phase | Meaning |
| --- | --- |
| `submitting` | The request may have reached the provider; no ID is known. |
| `preparing` | The ID is known; initial upload is incomplete. |
| `uploaded` | Files arrived; dependency or GPU checks may remain. |
| `ready` | The requested checks passed. |

Bindings also save the Python interpreter and dependency setup result. Completed uploads survive
later failures. Repeating an incomplete upload needs confirmation because it may overwrite files.
A project lock prevents concurrent launches; a separate lock protects short state writes.

Rent and resume finish in the original process, including sudo authentication. Preparation runs
in a separate user process unless `--wait` is set. It never rents or starts an instance.
Each instance has `job.json`, `status.json`, `setup.log`, and a worker lock.
An identical request reuses an active worker; different requests must wait.

The worker checks the project binding before setup. The CLI releases the project lock before
opening the dashboard so the worker can acquire it. The computer must stay awake and connected.
The dashboard reads saved steps once per second without polling the provider. Completion refreshes
the instance list once. `crig status` prints the same records for scripts.

Dashboard actions run after the UI returns a selection, then reopen the dashboard. Expected errors
remain visible without automatic retries. Editor actions reuse saved project settings and folders;
an explicit remote path creates a generic connection request.

## Remote setup and files

`bootstrap.sh` checks rsync and uv and can install them on Debian/Ubuntu images with root SSH.
Rsync uses a pinned archive and hash; uv uses Astral's installer. A remote lock prevents concurrent
installation. Logs go to `~/.cache/cloudrig/startup.log`.

`--startup FILE` replaces that script and runs before upload on each preparation. Project script
paths are relative to the project; other commands use the current directory. An empty script skips
startup. Plain `crig NAME ssh` skips default startup so it remains available for repair.

Uploads apply Git ignores, `.crigignore`, and built-in filename exclusions. `crig plan --files`
previews them without provider access. They do not inspect contents for secrets.
Later connections preserve remote edits and do not upload again.

`--setup` supports `uv.lock` and `requirements.txt`. Readiness normally checks `nvidia-smi`.
`framework = "pytorch"` also checks CUDA with the saved project interpreter. Packages in the
base image need not exist in the project's Python environment.

## Go modules

| File | Purpose |
| --- | --- |
| `main.go` | Check the invoking user and parse privileged commands. |
| `installation.go` | Check the running service protocol during self-test. |
| `models.go`, `store.go` | Define records, replace files, lock writes, and configure HTTP. |
| `providers.go` | Send requests and parse provider data. |
| `service.go` | Serve the Unix API and manage SSH signers. |
| `ssh.go` | Generate keys, pin hosts, restrict signing, and publish connection data. |
| `lifecycle.go` | Link credentials, rent, resume, delete, and recover rentals. |
| `peer_*.go` | Read socket caller identity on each OS. |

The ordinary API permits health and rental-status checks, offer and instance reads, and approved
stops. Spending, deletion, recovery, credential changes, and SSH approval need fresh sudo.
After sudo authentication, the helper prints its quote or deletion warning and proceeds without
another prompt. It refreshes prices before submission.
The installer applies sudo settings only to the helper and restores the previous installation
when replacement, validation, startup, or self-test fails.
See [security.md](security.md) for the quote limits, paths, and trust assumptions.

Instance lists must contain the expected array. Invalid collections or cursors are errors, not
empty accounts. Credential validation uses a temporary store before revoking grants and replacing
the live token. Vast rental lookup uses `ask_contract_id` to find the reviewed offer directly.

The protected journal stores pending names in `pending` and saved key associations in `requests`.
Recovery matches a provider ID and rental name before attaching the key. Clearing requires a
successful listing with no matching name and a human check of the provider console. Listings can
lag. Old RunPod receipts without a key association require manual recovery.

While a record is `connecting`, instance reads may finish key registration and the first host pin.
They cannot grant new access, restore revoked grants, or replace a completed pin.
The service publishes a signer only after connection setup finishes.

See [verification.md](verification.md) for completed checks and [test-plan.md](test-plan.md)
for required failure cases. Build commands are in the verification page.

## Provider request limits

The service shares account-list snapshots for five seconds across public instance reads.
Privileged operations and recovery always use fresh provider data. HTTP 429 responses carry a
retry delay to the CLI. The service honors `Retry-After`, or waits 30 seconds when it is absent.
Preparation checks normally run every ten seconds and wait through temporary rate limits until
the configured timeout. Cloudrig does not repeat rental, start, stop, or delete requests automatically.
