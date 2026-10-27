# Security architecture: privilege separation

Version 0.2.2. No container/VM isolation is used. The boundary is the OS root identity and its
protected program, credential files, process memory, and sudo policy.

## Trusted component

`admin/` builds one native Go executable. The installer copies it to
`/Library/PrivilegedHelperTools/cloudrig-admin` on macOS or
`/usr/local/libexec/cloudrig-admin` on Linux. Root owns the executable and its parent path. The
service never imports Python code, loads user modules, uses caller-selected executables, or reads
an agent-editable project file as authority.

`/var/db/cloudrig` is root-only. Provider keys, per-instance SSH private keys, allocation receipts,
connection records, and the local owner's UID/GID live there. Nothing in the normal user's config
can change the protected service's policy or credential paths.

The helper erases inherited environment before credential/network work, uses fixed provider HTTPS
origins, verifies TLS with the host trust store, refuses redirects, and uses fixed system executable
paths. It accepts no arbitrary URL, host private-key path, local shell program, or generic API call.

## Ordinary authority

The root service exposes one local Unix API with kernel peer-UID checks. Its enum permits only
search, instance reads, and stopping an approved resource. All other operations are rejected even
if the caller knows the protocol and rewrites the complete Python client. JSON fields are bounded
and unknown fields are rejected. Provider response environment/registry secrets are not returned.

Root publishes only public keys, pinned server identities, and per-instance signing sockets under
`/Library/PrivilegedHelperTools/cloudrig` on macOS and `/var/run/cloudrig` on Linux.
macOS's system `/var/run` directory can be group-writable, so it cannot satisfy the strict ancestor
checks used for public grants. The runtime directory uses the protected helper's parent instead;
the ownership policy has no group-writable exceptions.
OpenSSH agents run as root with the actual private keys and destination/user
constraints. The proxy accepts identity reads, sign requests, and `session-bind@openssh.com` only;
OpenSSH performs and enforces the cryptography. Mutating/importing keys and other agent extensions
are denied. Unbound arbitrary signatures are refused by OpenSSH, not a CLI convention.

A rental approval also authorizes connection setup for the allocated ID. An approved start authorizes
reconnecting that recorded instance. The protected journal marks this work `connecting`; instance reads
may finish its public-key registration and first host pin without another sudo operation. This flag is
written only by the privileged rental/start path. Unknown, revoked, and completed records cannot gain
or change SSH authority through a read. No signer is published while setup is pending. SSH setup can
resume after a helper restart; it never submits another allocation.

A user with a connection grant may authenticate to that approved server identity. That is intended
work authority, not private-key ownership. Host identities are SSH host keys, not an absolute
network-address attestation. A compromised remote machine can modify its own data and persist
processes; credentials on the host are a separate boundary.

SSH, VS Code, and rsync run unprivileged. The helper never copies user-selected files as root and
never executes an agent's command locally as root. Remote commands execute on the approved GPU.
Agent forwarding, X11, local commands, proxy hooks, and connection reuse are disabled. The normal
client receives no provider secret environment variables. It no longer uses keyring or user-key
fallbacks. CLI code integrity is not the spending-authority control.

## Privileged authority

Rent, start, delete, credential rotation, and SSH authorization enter the immutable helper through
sudo. The installer requires authentication and sets `timestamp_timeout=0` for the configured user,
not only for the helper command. The broader setting prevents a generic cached `sudo sh` from
bypassing a helper-only timestamp rule. Passwordless/`!authenticate` grants block installation.

Every client invocation also uses `sudo -k`; this is supplemental, not the security boundary.
Changing or removing that flag in Python does not change the installed sudo policy or give the
caller access to root credentials. `--yes` does not bypass privileged approval. The helper checks
its actual EUID and invoking user's sudo identity, validates the requested operation itself, and
requires terminal confirmation for resource/SSH-authority changes and provider-key registration
or rotation. Every request generates a fresh random approval challenge. Approval is consumed by
that single operation, never cached, and cannot authorize another resource or action. The helper
accepts no batch operation or blanket approval. A bare `APPROVE`, a previous request's code,
empty input, and overlong input cancel the request. Confirmation uses `/dev/tty`; client standard
input is disconnected. Key changes require confirmation before any secret input/storage.

Sudoers names only the supported helper verbs; there is no wildcard grant covering the helper's
daemon or arbitrary future commands. Each invocation accepts one operation with its exact
argument count. Read-only diagnostics require sudo authentication but no resource-change approval.

Privileged actions belong in a trusted human terminal. A password or user-presence approval given
to an attacker-controlled terminal is not a trustworthy approval path. No password capture,
password forwarding, `sudo -S`, `NOPASSWD`, or unattended root-authentication mechanism is provided.
An agent-editable CLI can also fake a password prompt in a human terminal. When treating the local
agent as hostile, invoke `/usr/bin/sudo -k --` followed by the absolute installed root-owned helper
path and the intended operation directly, without running the editable CLI as the approval entry
point. The installer/artifact must already be trusted. OS permissions do not prevent password
phishing by programs a human chooses to run.

The helper re-queries the GPU offer instead of trusting the client's GPU count or price. A changed
quote requires a new approval. Root receipts prevent silent repeat allocation after an ambiguous
network result. Recorded names remain reserved even if provider listings are stale or deletion
fails; only confirmed deletion releases the name. Start prices come from the existing Vast contract
or exact RunPod GPU/cloud type and verified integer count;
storage remains separately billed where not quoted. This is not a provider price lock or a total
spending cap. Resizing, restart, volumes, payments, automatic funding, and budget mutation are not
implemented and are not delegated through the ordinary interface.

## Installation and residual trust

Review/build the native artifact without root. Only install a trusted artifact from your own
terminal. Root installation is the bootstrap trust step; an attacker-controlled artifact cannot
be made trustworthy by a permissions wrapper. OSS releases should use reproducible builds and
signed/provenance-verified artifacts. The installer does not run Go, pip, npm, or project scripts
as root and does not auto-update the privileged binary from an editable checkout.

The root-level self-test checks actual denied file access as the configured user and refused
non-interactive sudo validation. Unit tests run without root and do not prove that the service or
sudo policy has actually been installed. Root installation and live-provider tests remain pending.

Old user-readable provider keys/private keys and logged-in provider browser sessions are outside
this boundary. Revoke legacy credential copies and use a new key through the root login flow.
Root/kernel/sudo/OpenSSH compromise is also outside it. Existing SSH sessions are not retroactively
revoked by closing a signing socket. Root can always read software secrets; no claim of absolute
immunity against every attacker is appropriate.

## Primary references

- [OpenSSH destination constraints](https://www.openbsd.org/openssh/agent-restrict.html)
- [ssh-add host/user constraints](https://man.openbsd.org/ssh-add)
- [Sudoers authentication and timestamps](https://www.sudo.ws/docs/man/sudoers.man/)
- [Rsync security advisories](https://rsync.samba.org/security.html)
