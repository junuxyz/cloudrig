# Agent access

Start with `crig plan /path/to/project --files`. An agent can preview the upload before you approve a rental.

Cloudrig protects its API keys. It does not isolate an agent from your files or limit commands on
an approved GPU. These permissions describe the current helper after installation.

## What an agent can do

| Without a new password | Scope |
| --- | --- |
| Read or change local files; run local programs | Whatever its OS account and agent settings allow. Cloudrig adds no local sandbox. |
| Search GPU offers; list account instances; check setup progress | No new Cloudrig approval. Instance reads can finish connection setup you already authorized. |
| Connect to an approved GPU | Run commands, install software, upload or download files, and change or delete remote files as the approved SSH user. That user may be root on the GPU. |
| Stop an approved instance | No new sudo authentication. This can interrupt running work. |

Approval belongs to the configured OS user, not to one agent or one project. Other programs running
as that user can use the same approved connection. SSH access is not restricted to a project folder
or a list of safe commands.

## What needs you

| Action | Required by the helper |
| --- | --- |
| Rent, start, or delete an instance | Fresh sudo authentication. The helper then proceeds without another prompt. Rent and start print a quote; deletion prints a data-loss warning. |
| Add or replace an API key; approve SSH to another instance; recover a rental record | Fresh sudo authentication. |
| Read Cloudrig's stored API keys or SSH private keys | Root access. The helper has no command to return them. |
| Run arbitrary commands as root on your computer | The helper does not support this. |

The SSH signer allows authentication to the approved host and remote user without exposing its
private key. Remote agents receive no forwarded signer. Revoking approval blocks new SSH
authentication; it does not close existing sessions.

## A normal workflow

1. Ask the agent to check requirements and preview the project with `crig plan PATH --files`.
2. Run `crig` in your own terminal for first-time setup. Enter your password and API key there. Setup does not rent a GPU.
3. Run `crig up /path/to/project --setup`. Choose a GPU and review the rental before authenticating with sudo.
4. Let the agent work on the approved GPU. Use `crig status` to check preparation. Stop compute with `crig down /path/to/project` when finished.
5. Download any outputs you need before deleting the instance. Confirm deletion yourself and check the provider console for remaining resources.

Closing the dashboard or editor does not stop compute. There is no automatic shutdown or total
spending cap. Storage charges may remain after compute stops.

## Limitations

Cloudrig does not force agents to use only `crig`. An agent can use an approved SSH connection
directly or run arbitrary remote commands through `crig NAME exec`. Asking an agent to use only
`crig` is an instruction, not an enforced permission limit.

Stricter controls are a [future milestone](milestones.md#agent-access-controls), with no scheduled release.

An agent with sufficient remote permissions can kill workloads or run shutdown commands inside
the GPU instance. Cloudrig does not require local sudo for those remote commands. Stopping compute
through Cloudrig also currently needs no new sudo authentication, as shown above.

An agent with a root shell, passwordless sudo, or a usable generic sudo login can bypass the helper.
Cloudrig preserves other sudo settings. For an untrusted local agent, use a separate OS account
without generic sudo access. Keys stored elsewhere remain readable according to their file permissions.

A modified CLI can imitate a password prompt. If an agent may have changed it, use the installed
helper directly from your own trusted terminal. Never send a password or API key in agent chat.

See [security.md](security.md) for helper commands and technical limits,
[agent setup](agents.md) for instructions to give your agent, and
[verification](verification.md) for installed-service checks that remain incomplete.
