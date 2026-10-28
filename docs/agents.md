# Set up Cloudrig with an agent

Read [what agents can do](agent-isolation.md) for the permission table and normal workflow.

Give your agent this instruction:

> Follow docs/agents.md. Check requirements, install Cloudrig, and preview my project upload.
> Keep passwords and API keys in my terminal. Give me one command to review and rent a GPU.

## Install

Read [security.md](security.md). Check the [requirements](getting-started.md#first-time-setup)
and report missing or outdated tools. Do not change system tools or sudo policy without permission.
Install VS Code only if requested.

Install a trusted release wheel matching the user's platform:

```sh
uv tool install /absolute/path/to/cloudrig-VERSION-PLATFORM.whl
```

Follow [release verification](distribution.md). For a source checkout, use the contributor instructions.
Give the user one command:

```sh
crig
```

The first launch guides protected service installation and account login.
`crig doctor` checks tools and the installed service. A missing editor is acceptable with `--no-code`.

The user must run it in their own terminal. Never request, log, or store their password or API key.
Do not run setup through an agent-controlled password prompt.

## Preview the project

Use existing `crig.toml` settings and the user's requirements. Otherwise, defaults are one GPU,
24 GB memory, 40 GB disk, and a quoted price up to $1/hour. The price filters offers; it does not
cap total spending or schedule shutdown. New project rentals require Vast.ai.

```sh
crig plan /absolute/path/to/project --files
crig plan /absolute/path/to/project --json
```

Review unexpected upload files with the user. Exclusions match filenames, not secret contents.
Do not print credentials or complete authenticated provider responses. Do not remove exclusions
to upload credentials.

## Hand over

Give the user one command:

```sh
crig up /absolute/path/to/project --setup
```

Add `--no-code` if needed. Add other flags only for requirements the user supplied.
The project browser asks the user to choose and confirm a GPU. Never rent without permission.
`--json` and `--plain` are read-only. `--numbered` uses numbered choices.

Use `crig status` to follow progress. Reconnect with `crig up PATH`; stop with `crig down PATH`.
Reconnection preserves remote files. Closing the editor does not stop billing, and storage may
still cost money after compute stops.

Do not repeat uploads or rent a replacement after an error. For an uncertain rental, follow
[recovery](guide.md#recover-a-failed-setup-or-uncertain-rental). Never use paid resources to test installation.
