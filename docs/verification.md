# Verification status

Current architecture: 0.2.2, native privileged helper and unprivileged Python client.

## Architecture cleanup

The [structural refactor](architecture.md) passes 80 Python cases, the native race suite, `go vet`,
Ruff, and all nine deliberate-regression checks. Native macOS and Linux amd64 builds and the
Python package build succeed. A read-only demo command also runs from the built wheel.
No privileged installation, paid rental, or live provider mutation was performed.

## Reliability test execution

The [plan](test-plan.md) was written before the new tests. Tests exercise real local HTTP/Unix
servers, persistent files, independent processes, native OpenSSH clients/agents, and SSH test
servers. Provider servers use synthetic credentials and local endpoints only.

Earlier reliability campaign on macOS:

- Python: 49 tests pass. Parallel subprocesses preserve every committed preference; a failed
  atomic rename leaves the original state readable and removes temporary debris.
- Native: 36 top-level tests pass, plus parameterized subtests and two fuzz seed suites. The
  complete native suite passed three shuffled runs under the race detector in 24.7 seconds.
- Native statement coverage is 72.3%. This is diagnostic, not the acceptance criterion; unexecuted
  root dispatch, human-terminal secret entry, and installed sudo policy remain explicit limits.
- Agent-frame fuzzing ran 454,155 inputs over 10 seconds. Instance parsing ran 401,284 inputs
  over 10 seconds. Both used two workers and completed without a failing input.
- All nine selected deliberate regressions are detected. The harness requires a failed behavior
  assertion; a compilation failure does not count. [Machine-readable results](test-sensitivity.json)
  list the disabled protections and the tests that catch them. This is not exhaustive mutation coverage.

The tests exposed and now prevent:

1. Duplicate allocations when a known rental was absent from a stale provider list, including
   concurrent brokers and attempts after failed deletion.
2. Underquoted RunPod starts caused by missing, boolean, fractional, or nonpositive GPU counts.
3. A credential-login panic caused by a regex repetition unsupported by Go's regex engine.

Service lifecycle tests additionally cover revoke/regrant reconciliation and removal of actual
native-agent/API sockets after context cancellation or corrupt authority state. The service now
handles SIGTERM/interrupt through that cancellation path. The installed OS service has not run.

Reproduce the checks:

```sh
uv run pytest -q
(cd admin && go test -race -count=3 -shuffle=on -timeout=60s ./...)
(cd admin && go test -run='^$' -fuzz='^FuzzAgentFrames$' -fuzztime=10s -parallel=2 -timeout=30s)
(cd admin && go test -run='^$' -fuzz='^FuzzProviderInstance$' -fuzztime=10s -parallel=2 -timeout=30s)
uv run python scripts/check-test-sensitivity.py
```

Recovery tests cover failed requests and broker restarts on a running OS. They do not simulate
power loss or storage-controller faults. No root/sudo policy was changed to obtain these results.

## Checked without root

- Python client/UI suite: onboarding, provider choice, demo safety, fixed fresh-sudo invocation,
  no environment credential fallback, public-key-only SSH configuration, rsync round trips,
  unsafe input handling, and terminal interactions at 80×24 and 120×36.
- Native Go suite with race detection: ordinary API cannot rent/start/delete/modify keys or execute
  local commands; unknown JSON fields rejected; caller identity checked with kernel peer credentials.
- Provider secrets stay in fake root-role stores during tests; instance environment secrets and
  raw provider error bodies are absent from normal responses. Redirects do not forward credentials.
- Actual OpenSSH agent/client/server tests use generated fixture keys only. An approved SSH login
  succeeds through the proxy. Key import/deletion and unbound signature requests are rejected.
- Native provider quote tests confirm actual GPU count, disk cost, human cancellation, and price
  recheck after approval. Root request journals retain ambiguous requests across unrelated writes.
- Build and sudoers syntax checks. Native helper refuses direct execution by an ordinary UID.
- Consecutive operations require separate approval challenges. Reusing an earlier code, bare/bulk
  approval, blank input, and oversized input fail. Cancelling a credential change does not read
  replacement input or change the stored token. The client disconnects standard input from sudo.
- Linux amd64 cross-compilation succeeds. Linux service installation/runtime has not been tested.
- Python dependency audit and native `govulncheck` report no known vulnerabilities in the checked
  dependencies. These checks are not a full security audit.

The Go suite uses temporary stores and the current test UID to exercise its logic/protocol. That
is not a claim of root memory/file isolation on an already deployed system.

## Required after human root installation

The installer and `crig self-test` run the real installed checks:

1. The native helper, owner configuration, credential directory, and service are root-owned.
2. A process actually dropped to the ordinary UID cannot read a root-only fixture file.
3. The ordinary UID cannot validate sudo non-interactively with the installed timestamp policy.
4. Review the effective sudo policy for all other passwordless or broad-root entry points.

This privileged installation has not been run by the development agent. No actual root-owned
provider credential has been stored and no live paid instance has been created or changed.

## External contract evidence

The initial development checked public Vast GPU catalog/offer reads, its storage cost fields,
RunPod's official v2 OpenAPI request schema, default image manifest availability, and official SSH
key APIs. Live authenticated rent/start/delete and real remote VS Code/rsync/agent execution still
need a funded provider account and explicit human approval. Fixtures do not prove live billing.
