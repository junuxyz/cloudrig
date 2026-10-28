# Security design

The Python CLI runs as the local user. The Go helper runs as root and holds provider tokens,
SSH private keys, and rental records. This separation relies on the OS, sudo, and OpenSSH.

## Files and requests

| Location | Contents |
| --- | --- |
| `/var/db/cloudrig` | Root-only tokens, keys, records, and owner UID/GID. |
| `/Library/PrivilegedHelperTools/cloudrig-admin` | macOS helper executable. |
| `/usr/local/libexec/cloudrig-admin` | Linux helper executable. |
| `/Library/PrivilegedHelperTools/cloudrig` | macOS public keys, host pins, and signing sockets. |
| `/var/run/cloudrig` | Linux public keys, host pins, and signing sockets. |

Root owns the helper and its parent directories. Public runtime directories cannot be writable by
other users or groups. macOS avoids `/var/run` because it can be group-writable.
The helper clears its environment, verifies TLS, refuses redirects, and calls fixed provider URLs
and absolute system-tool paths. It accepts no arbitrary shell commands, API URLs, or private-key paths.

The Unix API checks kernel caller identity, limits request size, and rejects unknown fields.
It allows health and rental-status checks, offers, instance reads, and approved stops.
Responses omit provider credentials, registry secrets, and raw provider error bodies.

## SSH and credentials

Each approved instance gets a key restricted to its host key and remote user. OpenSSH enforces
those restrictions. The proxy allows identity reads, signing, and `session-bind@openssh.com`;
it rejects key changes and never returns private keys.

A rental or recovery authorizes connection setup for that instance. While `connecting`
is set, reads may register its public key and first host pin. They cannot grant unrelated access,
restore revoked grants, or replace completed pins. The signer appears after setup finishes.

VS Code and its SSH connection run as the ordinary user. Agent and X11 forwarding, local SSH commands,
proxy hooks, and connection reuse are disabled. Remote agents receive no local private key or
forwarded signer. Revocation blocks new authentication, not existing SSH sessions.

Login validates a candidate key in a root-only temporary directory. The live key stays unchanged
during validation. Success revokes the provider's old grants before replacing the token.
A failed final write can leave those grants revoked and require reapproval.

## Authentication and prices

Rent, stop, delete, recovery, credential changes, and new SSH approval require fresh sudo.
Installation applies `timestamp_timeout=0`, `authenticate`, and `PASSWD` only to the Cloudrig helper.
Other sudo commands keep their existing settings.
The helper checks the invoking user and command arguments. The client cannot skip sudo authentication.

After sudo authentication, the helper prints the operation and proceeds without another prompt.
Rent prints the helper's provider quote; delete prints a data-loss warning.
The helper rejects rental quotes above the client's reviewed maximum and refreshes the quote
before submission. It rejects a price increase or changed GPU conditions.
These checks do not cap later charges or freeze future prices. Review the intended action before entering your sudo password.

Pending requests block repeat rentals after uncertain responses. Recovery preserves the saved key.
Before clearing a request, check the provider console; an empty listing can be stale.
There is no automatic shutdown. Storage and transfers can add charges after compute stops.

## Installation and limits

Release wheels include the helper and installation scripts. Cloudrig checks their platform, version,
and SHA-256 hashes before requesting administrator authentication. These hashes detect damaged files;
they do not authenticate the publisher or protect against replacement of the whole package.
For updates, `crig update` verifies the official GitHub workflow signature and release tag with
GitHub CLI before installing the wheel. For the first installation, follow
[release verification](distribution.md) before trusting the downloaded wheel.

Run `crig` from your own terminal to install or update the bundled service. `crig update` updates the
command and then checks the service in a new process. Updating only the Python files does not replace
the installed helper. The installer copies the binary and does not run Go or Python as root.
The installer backs up the helper, owner record, sudo policy, service definition, and service state.
Failed replacement, policy validation, startup, or self-test restores the previous installation.
Provider tokens and rental receipts remain unchanged. If restoration fails, backups remain in
`/var/db/cloudrig-install-backup`; rerunning the installer retries restoration first.
An uncatchable kill leaves `/var/db/cloudrig-install.lock`. Check its recorded PID and confirm the
installer has stopped before removing that lock and rerunning. Backups do not guarantee recovery
from power loss or disk failure. The release workflow generates signed GitHub build attestations. It has not been run or verified
on a hosted repository. Apple signing, notarization, and reproducible-build verification are not implemented.

A modified CLI can imitate a password prompt. If an agent may have changed it, invoke the installed
helper directly with `/usr/bin/sudo -k --`, its absolute path, and the intended operation.
Never give an agent a sudo password, passwordless rule, root shell, or spending-capable provider session.

`crig self-test` checks that the configured user cannot read a root-only fixture or run the helper's
harmless authentication probe through non-interactive sudo. It also checks the running service
protocol. Local fixtures do not prove isolation on an installed system.
See [verification.md](verification.md) for the remaining checks.

A root shell, usable generic sudo timestamp, or passwordless arbitrary root access can bypass the
helper and read its credentials. Cloudrig leaves those account settings unchanged. Run untrusted
local agents in a separate OS account without generic sudo access.

Cloudrig does not protect credentials stored elsewhere or against compromised root, kernel, sudo,
or OpenSSH. The first SSH pin trusts the observed host key; independent provider attestation is
not implemented. An approved remote machine can alter its files and run persistent processes.

Cloudrig does not restrict agents to `crig` commands. Approved SSH access allows remote commands
as the approved user, including terminating workloads when that user has permission.
Shell commands, file transfers, and remote-agent launchers are deferred. See
[agent access limitations](agent-isolation.md#limitations).

## References

- [OpenSSH destination constraints](https://www.openbsd.org/openssh/agent-restrict.html)
- [SSH host and user constraints](https://man.openbsd.org/ssh-add)
- [Sudo authentication and timestamps](https://www.sudo.ws/docs/man/sudoers.man/)
