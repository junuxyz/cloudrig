# Agent authority

The selected design uses OS privilege separation. No Docker, VM, root shell for the agent, or
custom cryptography is involved.

| Resource/action | Ordinary AI may access it? |
| --- | --- |
| Project files and ordinary local tools | Yes |
| Read-only GPU search and instance listing | Yes, through the broker's enum |
| Stop a root-approved resource | Yes |
| Authenticate/execute on an approved GPU | Yes, through a destination-constrained signer |
| Public SSH key and approved server fingerprint | Yes; these are not secrets |
| Cloudrig SSH private key | No; root files and root OpenSSH agent memory |
| Cloudrig provider token | No; root files and root service memory |
| New rent/start/delete/key change | Fresh human sudo authentication and a separate one-operation approval |
| Arbitrary root command or arbitrary API request | No interface provided |

An agent may inspect/replace the unprivileged client and read the complete OSS source. Neither
changes the protected broker, its root-owned state, or the sudo policy. The ordinary Unix API
refuses mutating operations, and the signing channel cannot return private keys. Only OpenSSH's
session-bound, destination-constrained signing is delegated.

A remote AI receives no forwarded local signer and no private-key files. A local AI can use an
approved connection, but not extract the managed key. This protects cloudrig's newly managed
credentials, not unrelated keys already stored in the ordinary user's personal home.

Do not give an agent a sudo password, a passwordless rule, a pre-authenticated general root shell,
or a provider browser session with spending authority. These would bypass the intended OS
boundary. Human approval should happen in a separate trusted terminal, not an agent-owned PTY.
If the agent can modify the CLI, use `/usr/bin/sudo -k --` and the absolute installed protected
helper path directly. An agent-modified client can fake a password prompt even in another terminal.

Root installation must be reviewed and completed by the human. A prepared installer and passing
unprivileged tests are not evidence that root ownership or fresh-sudo policy is active yet.
