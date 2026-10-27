# Architecture

Cloudrig has two executables. The Python CLI owns the development workflow. The installed Go helper
owns provider credentials and the authority to allocate resources. This boundary comes from the
existing requirement that an agent running as the ordinary user cannot read spending credentials.

```text
crig
  cli.py                 market and instance commands
  workspace_cli.py       project commands and review
      workspace.py       allocate/resume, upload, install, probe, open
      project.py         requirements and local recovery records
  broker.py              typed provider operations over the helper protocol
      cloudrig-admin     provider requests and protected authority
  connections.py         ordinary-user SSH, VS Code, and rsync
```

## Python responsibilities

| Module | Responsibility |
| --- | --- |
| `cli.py`, `workspace_cli.py` | Parse arguments, choose actions, review effects, and print results. |
| `tui.py`, `workspace_tui.py` | Display data and return a selection. The UI does not allocate resources. |
| `workspace.py` | Execute a selected rental or resume an existing binding. Takes explicit options rather than parser state. |
| `project.py` | Load project requirements, filter offers, and persist typed bindings. Owns the project lock. |
| `broker.py` | Local transport and normalized provider operations. Owns provider image and billing defaults. |
| `connections.py` | Build and execute SSH, editor, and transfer commands. |
| `state.py` | Atomically read and write ordinary-user preferences and recovery records. |
| `models.py` | Immutable offers, instances, and rental requests with boundary validation. |
| `security.py` | Child-process environment and filesystem policies shared by project and transfer commands. |
| `terminal.py` | Shared console and ordinary local confirmation. |
| `demo.py` | Fictional data for previews. Uses the same image defaults as the real client. |

The command layer owns interactive decisions. The workspace workflow does not import a parser or
a terminal application. Progress messages use the shared console; there is no event bus or UI adapter
framework. Adding another frontend does not currently justify either.

Project settings resolve in order: built-in defaults, global provider preference, `crig.toml`, command
overrides. `crig init` generates values from the same project object. Demo mode reads no global state.
Images are pinned defaults that users may override. They are not an automatically updated compatibility matrix.

## Workspace recovery

`Binding` stores a phase, instance ID, selected offer, and prepared Python environment:

```text
submitting  →  preparing  →  uploaded  →  ready
   no ID        has ID      files sent   CUDA checked
```

The submission record is written before the paid request. The ID is written before waiting for SSH.
An ambiguous submission stays unresolved until the user reconciles it. A known instance that disappears
never becomes permission to allocate a replacement.

After upload completes, later failures preserve that phase. Retrying the CUDA probe does not upload
over remote edits. Dependency setup records its interpreter after success. Each transition writes a
complete immutable binding, so an omitted field cannot accidentally erase the instance ID.

The command layer holds a per-project lock across selection and execution. State writes use a separate,
short-lived global lock so unrelated projects cannot overwrite each other's records. Local records help
recovery; they grant no privileged authority. The helper maintains its own allocation journal.

## Native helper responsibilities

The helper remains one Go package and one binary:

| File | Responsibility |
| --- | --- |
| `main.go` | Process entry, installed-owner checks, and privileged command dispatch. |
| `models.go` | Protocol and persisted record types. |
| `store.go` | Protected storage, atomic writes, locks, and HTTP client configuration. |
| `providers.go` | Vast/RunPod requests, normalization, quotes, and lifecycle endpoints. |
| `service.go` | Ordinary-user Unix API and signer lifecycle. |
| `ssh.go` | Per-instance connection grants and OpenSSH signing proxy. |
| `lifecycle.go` | Credential changes, rental, start, and deletion with approval and journaling. |
| `approval.go` | Single-operation terminal approval. |
| `peer_*.go` | OS-specific Unix peer identity. |

There is no provider plugin framework. Two concrete providers share the existing normalized types.
The ordinary API permits search, reads, and stopping approved instances. Spending and credential
changes require the installed privileged command. See [security.md](security.md) for the threat model.

## Keeping changes small

Validate untrusted input once at the appropriate boundary. Domain code can rely on an immutable
`Instance` having a valid ID. Private-key parameters and clipboard connection paths were removed
because the protected grant model no longer uses them. Confirmation, quoting, journal, and transfer
checks remain because they protect an observable failure or an actual authority boundary.

Add job supervision or idle shutdown when implementing those features. The current code contains no
scheduler, agent orchestration layer, or speculative interfaces for them. First extract a shared
abstraction when a second implemented use case needs it.

## Verification

```sh
uv run pytest -q
uv run ruff check src tests
cd admin
go test -race ./...
go vet ./...
```

From the project root, `./scripts/build-admin.sh` builds the helper and `uv build` builds the Python
package. `uv run python scripts/preview-workspace.py` renders the real demo terminal screens.

The maintained suite covers command behavior, workspace recovery, provider contracts, and authority
boundaries. Tests use fixtures and local tools. Paid provider execution and the
installed root boundary still require the separate verification described in [verification.md](verification.md).
