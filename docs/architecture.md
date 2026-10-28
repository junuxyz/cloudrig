# Architecture

The Python CLI displays offers and instances, submits reviewed rentals, opens VS Code, and manages
stop and delete actions. The installed Go helper owns API keys, SSH keys, and rental records.

| Module | Responsibility |
| --- | --- |
| `cli.py`, `tui.py`, `models.py` | Commands, dashboard, checkout, and validated data. |
| `broker.py`, `onboarding.py` | Protected helper requests and account setup. |
| `connections.py` | Constrained SSH configuration and VS Code connections. |
| `installation.py`, `updates.py` | Bundled service installation and verified release updates. |
| `state.py`, `security.py`, `terminal.py`, `demo.py` | Preferences, local file permissions, terminal prompts, and demo data. |

Project configuration, transfers, startup scripts, remote commands, dependency setup, and preparation
workers are absent. Their previous implementation is on `deferred-project-workflows`.

## Protected operations

Protocol 4 allows public health, offer, instance, and rental-status reads. Stop, delete, rent,
credential changes, SSH authorization, and recovery require fresh sudo. Start is unsupported.
Instance reads may complete SSH setup only for an already approved rental.

Public account reads share a five-second snapshot. Paid operations and recovery read fresh data.
HTTP 429 includes the provider's retry delay, with a 30-second default. Editor connection waits
through temporary limits until its timeout. Mutations are never automatically repeated.

## Rental records and installation

Pending requests prevent duplicate allocations when a provider response is lost. Records retain
older connection metadata so existing rentals and receipts survive the reduced feature set.
API keys and private keys remain root-only. The SSH signer returns signatures restricted to the
approved host and user, not private keys. See [security](security.md).

Platform wheels contain the helper and installer. `crig` compares the installed service with its
bundle and requests an update when needed. Installation backs up the existing service and policy
and restores them on failure. It preserves account credentials and rental records.
