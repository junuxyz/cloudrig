# Start here

Download your platform wheel from [Releases](https://github.com/junuxyz/cloudrig/releases)
into an empty folder, then run:

```sh
uv tool install ./cloudrig-*.whl
crig
```

This installs `crig` globally. Run `crig` from any folder after the initial setup.
The first launch guides account setup, then opens the dashboard. To try it without an account:

```sh
crig --demo
```

## First-time setup

Requires macOS 13+ or Linux on ARM64 or x86-64, uv, Python 3.11+, system OpenSSH 8.9+, and rsync 3.5.0+.
Release wheels include the protected service. No Go installation or source folder is needed.
For a source checkout, follow [Contributing](../CONTRIBUTING.md).
On macOS, install current rsync with `brew install rsync`. VS Code needs Remote - SSH.
Use `--no-code` on project commands to skip the editor.

Choose Vast.ai for projects. Create an account, add credit, and enter a new API key in the
protected prompt. RunPod currently supports marketplace commands only.
The login screen links to [Vast.ai keys](https://console.vast.ai/manage-keys/)
or [RunPod settings](https://console.runpod.io/user/settings) if you need to create a key.

Setup checks the bundled installation files and asks before installing the protected service. Only Cloudrig's helper requires fresh sudo
authentication; other sudo commands keep their existing settings. Read [security.md](security.md).
Setup does not rent a GPU. Repeating setup replaces the key and revokes that provider's SSH approvals.
Run `crig` after installing a newer wheel to update the service without replacing your API key.
Use `crig doctor` to check an existing setup without changing it.

To run account setup separately from another folder:

```sh
crig setup
```

## Open a project

```sh
crig up /path/to/project --setup
```

Choose a GPU, review the rental, and authenticate with sudo. The helper then proceeds without another prompt.
The first connection uploads files and installs detected uv or pip dependencies.
Later connections reuse the GPU and preserve remote edits.

The dashboard shows progress. Click View setup or press `g` for details.
You can close it and return later. Keep this computer awake and connected during preparation.
Add `--numbered` for numbered GPU choices.

When finished:

```sh
crig down /path/to/project
```

Closing the editor does not stop billing. Storage charges may remain after compute stops.
See [what agents can do](agent-isolation.md), [agent setup](agents.md),
[project settings](project-workspaces.md), or the [command reference](guide.md).

## Updates

The dashboard shows a notice when a newer stable release is available. Run:

```sh
crig update
```

Updates require GitHub CLI for release-signature verification. Cloudrig keeps your API keys and
asks for administrator authentication only when its protected service needs installation.
See [distribution](distribution.md) for first-install verification.
