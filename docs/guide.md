# Commands

Run `crig` for the dashboard. First use guides installation and account login.

## Browse and rent

```sh
crig --gpu
crig --gpu 5090
crig --rented
crig --demo
```

Select an offer to review its name, disk, image, and hourly quote before rental.
Search matches GPU names without regard to case. Sort by price, VRAM, region, or GPU count.
A price with `+` excludes unquoted storage. `--count` is a minimum on Vast.ai and exact on RunPod.
`--disk` sets the disk size used for offer quotes. For scripts, use `--json` or `--plain`.

Use `--provider vastai` or `--provider runpod` to select an account for one command.

## Connect, stop, delete

```sh
crig training code
crig training stop
crig training destroy
```

Names and IDs both work. The same actions are available from an instance's dashboard menu.
VS Code opens `/workspace` using protected SSH credentials. It requires `code` and system OpenSSH 8.9+.
There is no project upload, dependency installation, startup script, or remote-agent launcher.
If a provider temporarily limits requests, editor connection waits before checking again.
A connection failure leaves the GPU rented and never creates a replacement.

Rent, stop, and delete require fresh sudo authentication. Deletion also asks for confirmation
before authentication. The helper rechecks the rental quote before submission and rejects increases.
There is no total-spending cap or automatic shutdown. Closing the editor does not stop compute.

## Accounts and checks

```sh
crig setup
crig login
crig doctor
crig self-test
```

Run authentication commands in your own terminal. API keys are stored by the protected service.
Login shows the provider's key-generation page. Replacing a key revokes that provider's SSH approvals.
`doctor` checks the service; `self-test` also checks installed credential isolation and sudo settings.

An uncertain rental stays recorded to prevent another allocation with the same name. Check the
provider console before retrying. If recovery is needed, the installed helper supports
`recover PROVIDER NAME ID` to attach the matching instance, or `recover PROVIDER NAME -` to clear
an absent allocation. Use its absolute path with fresh sudo as described in [security](security.md).
Recovery never rents, starts, stops, or deletes resources.

## Update and uninstall

```sh
crig update
crig update --wheel /path/to/cloudrig-VERSION-PLATFORM.whl
crig uninstall
```

Updates verify the official GitHub workflow signature and release tag before installing.
Running `crig` then updates a mismatched protected service after your approval.
`crig setup --service-only` can finish an interrupted service update without replacing API keys.

Uninstall removes the command and service but keeps protected credentials and receipts for recovery.
It does not stop or delete provider resources. See [distribution](distribution.md) for release verification.

Project workflows and advanced remote tools are preserved on `deferred-project-workflows`.
See [milestones](milestones.md).
