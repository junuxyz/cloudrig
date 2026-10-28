# Command reference

For first use, [start here](getting-started.md).

## Try it without an account

```sh
crig --demo
crig --demo --gpu 5090
crig up . --demo --vram 32
```

Demo mode never rents, uploads, opens an editor, or writes configuration.
See [dashboard controls](design.md#controls) for keys.

## Install and connect an account

Install the wheel for your macOS or Linux platform with uv:

```sh
uv tool install ./cloudrig-*.whl
crig
```

Use a folder containing only the wheel you want to install. The command works globally.
The first launch installs the bundled protected service after your approval, then connects an account.
No Go installation or source path is needed. Each service installation serves one local user.
Run `crig setup` to connect or replace an account separately.

Project transfers require rsync 3.5.0+ and system OpenSSH 8.9+.
On macOS, use `brew install rsync`. VS Code needs Remote - SSH; `--no-code` skips it.
The helper uses OpenSSH tools in `/usr/bin`, regardless of the user's PATH.

Installation requires fresh sudo authentication for Cloudrig's helper and preserves other sudo settings.
Read [security.md](security.md). Use a new provider key and revoke older user-readable copies.
Never give an agent your password or key. If an agent may have modified the CLI, invoke the installed
helper directly from your own terminal as described in the security page.

To choose a provider or replace its credentials separately:

```sh
crig --set vastai
crig login
```

## Open a project on a GPU

```sh
crig plan /path/to/project --files
crig plan /path/to/project
crig up /path/to/project --setup
crig up /path/to/project
crig down /path/to/project
```

The first `up` rents, uploads, and prepares the project. Later calls reconnect without uploading.
The dashboard shows background progress; `crig status` prints it for scripts.
Use `--wait` to stay in the terminal or `--numbered` for numbered GPU choices.

[Project settings](project-workspaces.md#settings) cover memory, disk, price filters, images,
startup scripts, and optional PyTorch checks. New project rentals currently require Vast.ai.
Closing the editor does not stop billing. There is no automatic shutdown.

## Review and control uploads

Uploads follow Git ignores, including reinclusions, plus built-in filename exclusions.
`.git`, common credentials, virtual environments, and caches are excluded.
Files needing Git history during a remote build require separate setup.

Add extra rsync exclusions in `.crigignore` inside the local source directory:

```text
data/
checkpoints/
outputs/
private-config.json
```

These rules cannot reinclude files blocked by Git or built-in exclusions.
Preview with `crig plan PATH --files`. Filename checks do not detect every secret.

## Manage instances directly

```sh
crig --gpu
crig --gpu 5090
crig --rented
crig training ssh
crig training code
crig training exec -- nvidia-smi
crig training agent codex
crig training push ./ /workspace/project/ --dry-run
crig training push ./ /workspace/project/
crig training pull /workspace/outputs/ ./outputs/
crig training stop
crig training start
crig training destroy
```

RunPod search uses `crig --provider runpod --gpu QUERY`. Search matches substrings regardless of
case. A price with `+` excludes unquoted storage. Marketplace `--count` means a minimum on Vast.ai
and an exact count on RunPod; project counts are exact.

Rent, start, destroy, account changes, SSH approval, and recovery require fresh sudo.
Approved connections and stops do not. After sudo authentication, rent, start, and destroy proceed
without another prompt. The helper prints its quote or deletion warning before submission.
Deletion removes instance-local files; pull outputs first.

Plain SSH skips default startup. Other preparation uses a script that installs rsync and uv when
needed on Debian/Ubuntu images with root SSH. Logs go to `~/.cache/cloudrig/startup.log`.
To replace the script:

```sh
crig startup > startup.sh
# Edit startup.sh, then:
crig up . --startup startup.sh
```

Custom scripts run before upload on each preparation. An empty script, such as `/dev/null`, skips
startup. Write commands that tolerate repeated execution.

## Recover a failed setup or uncertain rental

Retry setup on the same instance. Cloudrig never rents a replacement after a setup error.
For a temporary provider request limit, preparation waits before checking again. If its timeout
expires, reconnect to the existing instance with `crig NAME code`; do not rent another GPU.
A different request must wait for an active setup to finish.

For a lost rental response, check the provider console first. Use the rental name from the error:

```sh
crig recover RENTAL_NAME --attach INSTANCE_ID
crig up /path/to/project --attach INSTANCE_ID
```

After verifying that no allocation exists, or that it was deleted outside Cloudrig:

```sh
crig recover RENTAL_NAME --clear
crig forget /path/to/project
```

Recovery never rents, starts, or deletes resources. Clearing refuses a listed instance, but listings
can lag; the console check is still required. Older RunPod receipts without saved key associations
need manual recovery.

Cancelled authentication is retryable only when the helper confirms no request was submitted.
If the helper is unreachable, the request stays unresolved.

## Update

```sh
crig update
```

Requires uv and GitHub CLI. Cloudrig downloads the newest stable wheel from
[junuxyz/cloudrig](https://github.com/junuxyz/cloudrig/releases), verifies its signed build identity
and release tag, then installs it. Verification failure leaves the existing installation unchanged.
For a wheel you already downloaded, use `crig update --wheel PATH`.

This installs the selected wheel, then uses the new command to update the protected service.
It keeps API keys and rental records. If service installation is cancelled or fails, finish with
`crig setup --service-only`. Running `crig` also checks that its installed service matches the package.

The dashboard checks for updates in the background at most once every 10 days.
Offline checks stay silent. Demo mode, plain output, and JSON output do not check for updates.
For source development, follow [Contributing](../CONTRIBUTING.md).
See [release verification](distribution.md) before installing downloaded packages.

## Uninstall

```sh
crig uninstall
```

This removes the command, service, and sudo policy after confirmation and administrator authentication.
It keeps credentials and receipts in `/var/db/cloudrig` and local SSH aliases for recovery.
It does not stop provider resources. Check the account for charges.

## Development

See [Contributing](../CONTRIBUTING.md), [architecture](architecture.md), and [verification](verification.md)
for checks and remaining limits. Automated tests never rent paid GPUs or modify real accounts.
