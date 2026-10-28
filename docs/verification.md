# Verification

From the repository root:

```sh
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests scripts/preview.py
(cd admin && go test -race ./...)
(cd admin && go vet ./...)
./scripts/build-admin.sh
uv build
```

Tests use temporary files, synthetic credentials, local servers, separate workers, and real
OpenSSH tools. They cover recovery, prices, credential replacement, constrained signing,
editor credentials, lifecycle operations, and terminal controls. They do not rent GPUs or install a root service.

Platform wheels bundle the native helper, installation scripts, and file hashes. Local tests check
bundle integrity, installation cancellation, service updates, and preservation of account setup.
The release workflow builds four target wheels and prepares signed GitHub attestations.
Hosted builds and attestation verification have not been run from this checkout.

## Remaining checks

- Install, update, and uninstall on macOS and Linux. Check ownership, signing access, and sudo policy.
- Run `crig self-test` on the installed system; test-user fixtures do not prove root isolation.
- Complete a real rental, editor connection, stop, and deletion. Confirm resource state and charges in the provider console.
- Verify the reduced core interface on narrow and wide terminals.

Installer tests run the full script against temporary paths and simulated macOS/Linux services.
They check restoration after failed file replacement, policy validation, service startup, and
self-test. They also check retained backups after failed restoration and recovery after a killed
installer. Provider tests check price changes before submission.

Mocked responses do not prove live API compatibility. Recovery tests cover process and request
failures, not power loss or disk faults.
See [security.md](security.md) and the [test plan](test-plan.md).

Project and transfer tests are preserved with their implementations on `deferred-project-workflows`.
