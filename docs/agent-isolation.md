# Agent access

Cloudrig protects its stored API keys and SSH private keys. It does not isolate an agent from
other files available to its OS account.

| Action | Access |
| --- | --- |
| Search offers and list instances | No new authentication. |
| Open an approved editor connection | No new authentication. |
| Rent, stop, delete, replace credentials, recover, or authorize another connection | Fresh sudo authentication. |
| Read Cloudrig's API keys or private keys | Root access; the helper has no command to return them. |

## Workflow

1. Let an agent install a verified wheel and check requirements.
2. Run `crig` yourself to install the service and connect your account.
3. Review and approve a rental, then open VS Code from Instances.
4. Stop or delete the GPU when finished.

## Limitations

Cloudrig does not force an agent to use only `crig`. Programs under the configured OS user can use
an approved SSH connection. The remote user may be root and can alter files or terminate workloads.
Shell, transfer, and agent-launch commands are not part of the current CLI.

Generic root access, a usable generic sudo login, or passwordless sudo can bypass credential protection.
Cloudrig preserves other sudo settings. Use a separate OS account without generic sudo access for
untrusted local agents. A modified CLI can imitate a password prompt; use the installed helper
from a trusted terminal if the CLI may have been modified.

See [security](security.md), [verification](verification.md), and [future controls](milestones.md).
