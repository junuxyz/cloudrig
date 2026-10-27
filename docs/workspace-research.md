# A project workspace for Cloudrig

Researched on October 27, 2026. This compares documented workflows and public source code.
It is not a live rental benchmark or a security audit of the alternatives.

## Recommendation

The pain is real. Renting a GPU and reaching a useful development environment still requires
decisions about hardware, storage, images, SSH, project files, dependencies, and cleanup.
But connecting a rented GPU to VS Code is already available in several tools.

For your personal goal, try the unofficial Vast.ai VS Code extension and Vastly before committing
to more infrastructure work. The extension directly targets search, rent, and Remote SSH inside
the editor. Vastly targets getting an existing instance ready for your project. If either solves
your recurring work, using it is a better result than maintaining Cloudrig.

If those tools fall short, keep Cloudrig's work on project continuity, recovery, and the existing
separation between agent work and spending authority. The proposed everyday interaction is
`crig up` inside a project. Hardware browsing remains available for the occasions when you need it.

## What already exists

| Option | What its documentation/source provides | What to evaluate for your use |
| --- | --- | --- |
| Official Vast CLI | `search offers`, `create instance`, `launch instance`, SSH key commands, `ssh-url`, file copy, lifecycle commands. `launch instance` submits an allocation request. | Enough to script the pieces. The inspected launcher returns the allocation response; it does not upload this repo, verify CUDA, and launch VS Code. |
| Vast's VS Code guide | Remote SSH setup and editor connection after keys are configured. | Official support for using VS Code exists. The documented flow still includes connection setup. |
| Unofficial Vast.ai VS Code extension | Search and create wizard, instance tree, generated SSH config, readiness wait, Remote SSH launch, lifecycle commands. | Closest direct substitute if you mainly want to rent and open VS Code. Requires a registered SSH key. It identifies itself as unofficial. |
| Vastly | SSH config sync, project clone/setup, dependency detection, VS Code/Cursor opening, repeat-run setup skipping, file transfer. | Closest substitute for project preparation. Its README enables agent forwarding; review that against Cloudrig's deliberate no-forwarding policy. |
| NVIDIA Brev | `brev start` from a repo or local path, setup scripts, `brev open` for VS Code with Remote SSH extension installation. | Strong reason to switch if its available instances and account workflow meet your needs. Do not assume it covers your preferred Vast offers. |
| SkyPilot | GPU development cluster launch, local workdir sync, stable SSH naming, VS Code access, autostop. | Worth evaluating when multi-cloud execution and repeated training jobs matter. It adds a larger tool and cloud setup workflow. |

Sources for the comparison:

- [Official Vast CLI launcher source](https://github.com/vast-ai/vast-cli/blob/master/vast.py).
- [Official Vast CLI quickstart](https://cloud.vast.ai/cli/).
- [Vast's SSH and VS Code guide](https://docs.vast.ai/guides/instances/connect/ssh).
- [Unofficial Vast.ai VS Code extension](https://marketplace.visualstudio.com/items?itemName=UnofficialVastAIVSCodeExtension.vastai-vscode-unofficial).
- [Vastly source and README](https://github.com/seamus-fallows/vastly).
- [Brev instance management](https://docs.nvidia.com/brev/latest/cli/instance-management)
  and [VS Code integration](https://docs.nvidia.com/brev/latest/guides/development-tools/vscode-setup).
- [SkyPilot interactive development](https://docs.skypilot.ai/en/latest/examples/interactive-development.html).

The absence of an editor-opening step in the inspected Vast launcher is evidence about that
command, not proof that no other Vast integration exists. These tools' documentation does not
establish real-world reliability or time saved. Those need hands-on comparison.

## What to change in Cloudrig's design

The existing flow starts with the marketplace. That asks you to choose infrastructure before it
knows the project. Move the default starting point to the current directory and remember its
remote allocation. Ask for VRAM per GPU, GPU count, a location preference, and a price filter.
Do not pretend a generic model-size estimate proves that training will fit.

Opening an empty `/workspace` is only part of the task. Upload the project on its first connection,
open its actual remote folder, and preserve remote edits on future connections. A failed SSH wait
must retain the instance ID. An ambiguous paid request must require reconciliation before retrying.

Cloudrig's largest onboarding cost is its privileged installation. The current installer changes
sudo caching for the whole account. That deserves explicit product review before wider use.
It buys a specific boundary against processes running as the ordinary user, including local coding
agents. A user-readable token or ordinary keychain wrapper does not preserve that same boundary.
Keep the current policy in this prototype. A future dedicated approval UI could reduce interaction
cost while retaining human approval of new spending. Do not hide a security-policy change in a UI cleanup.

For provider integration, prefer a maintained SDK or CLI when it supplies the required operations.
Keep workflow policy in Cloudrig. Reimplementing catalogs, billing, and provider APIs indefinitely
adds maintenance without directly improving the development loop. The protected helper must still
control the precise operations it authorizes; arbitrary provider CLI execution is too broad there.

## What this branch implements

```sh
# No credentials, installation, or paid GPU needed
uv run crig up . --demo --vram 32
uv run crig plan . --demo --vram 32 --region JP
uv run crig plan . --demo --json

# Optional per-project defaults
crig init .

# Requires the existing protected service and account setup
crig up . --vram 32 --max-hourly 0.80
crig up .                  # Reconnect to the same instance and remote folder
crig down .                # Confirm, then stop this project's compute
```

The review shows matching complete compute/disk quotes and a session estimate. VRAM is per GPU,
and count is exact. Lowest price means the lowest quoted hourly price among matching results.
It does not mean fastest GPU, lowest latency, or lowest total job cost. Location is a text filter;
the prototype does not measure latency.

The live path checks local SSH, rsync, and the VS Code extension before allocation. It then uses
the existing protected rental flow, saves the ID before SSH, uploads with the existing credential
exclusions, checks `nvidia-smi` and PyTorch CUDA, and opens the project folder. `--setup` opts into
the detected `uv sync --frozen` or requirements-file install. The image must already have the
required installer. Successful setup remembers its Python environment for future connections.

Receipts distinguish submission, preparation, upload completion, and readiness. Failed CUDA checks
do not cause another upload. An incomplete upload requires confirmation before retrying. Ambiguous
allocation can be reconciled with `crig up --attach ID` after checking the provider account and
authorizing that instance. Neither missing instances nor changed requirements silently rent replacements.
The project lock serializes concurrent `up` operations on this machine.

The existing marketplace UI and its commands remain available. Demo mode simulates the final result
and never rents, uploads, launches VS Code, or writes configuration.

## Changes with the highest expected value

| Priority | Addition | Why it helps | Acceptance criterion |
| --- | --- | --- | --- |
| Next | Durable stop deadline approved at allocation | Prevents forgotten overnight compute. A laptop timer is insufficient. | With the laptop offline, an observed stop occurs by the configured deadline; remaining storage charges are shown. |
| Next | A small, maintained development image | Reduces repeat dependency installs and incompatible Python/CUDA combinations. | A clean project reaches a successful CUDA probe across the supported GPU families. |
| Next | Explicit output pull with conflict preview | Keeps experiments and checkpoints when compute changes. | A pull never silently overwrites newer local work; the user can inspect the proposed changes. |
| Later | External checkpoints and dataset/model caches | Makes the workspace less dependent on one rented machine and avoids repeated large downloads. | Replace the GPU and resume a saved experiment using verified remote artifacts. |
| Later | Measured host qualification | Rejects broken GPUs, unusable SSH, full disks, and slow storage before calling a workspace ready. | Failed hosts produce actionable diagnostics and preserve allocation state for cleanup. |
| Later | Thin VS Code entry point | Makes `Open this project on a GPU` available without leaving the editor. | Uses the same project workflow and approval policy, with no second implementation of rental logic. |

Runpod's CLI already documents `--stop-after` and `--terminate-after`; provider-supported lifecycle
controls should be investigated before building another scheduler.
[Runpod Pod commands](https://docs.runpod.io/runpodctl/reference/pod).

The potential 2x improvement is removing repeated setup and connection work. The potential 10x
improvement is recreating a ready project, dependencies, data references, and checkpoints across
replaceable GPUs. These are hypotheses, not measured speedups. More marketplace charts or more
providers would not address the current pain as directly.

Measure cold launch, warm reconnect, and recovery after an interrupted connection. Record active
human time separately from provider boot and download time. Compare Cloudrig against the extension
and Vastly using the same project and equivalent hardware. If an existing tool is simpler and
reliable enough, adopt it.

## Verification and current limits

The initial prototype passed 24 new behavior and terminal interaction cases and the combined 77-test
Python suite. The native suite passed under the race detector, and `go vet` passed.
Ruff passes for the Python code and preview script. Real terminal rendering was inspected at
desktop and compact sizes. The local machine has rsync 3.5.1 and VS Code's Remote SSH extension.

No paid GPU was rented, no provider account was changed, and the privileged service was not
installed. Live provider allocation, real project upload, dependency installation, CUDA execution,
and VS Code connection still need a funded, human-approved smoke test. Fixture tests do not prove
provider reliability, live prices, or billing behavior.

The project launcher currently requires complete disk quotes. The current Runpod catalog adapter
does not provide one, so new project launches there have no eligible offers. Use the existing
Runpod marketplace workflow until its quote contract is implemented. Do not invent a storage rate.

`max_hourly` filters search quotes. The protected helper independently refreshes and confirms the
final price; this setting is not a hard billing cap. `hours` estimates cost and does not stop
anything. Storage and data transfer may cost extra. There is no automatic shutdown, bidirectional
sync, continuous sync, checkpoint backup, or cross-provider failover in this branch.

The initial upload excludes `.git`; remote Git history and private-repo authentication are not
configured. Content-based secret detection is not implemented. Setup inference supports uv and
requirements files only. Project bindings are local-path-specific, so moving a repo requires an
explicit reattachment. Existing bindings intentionally need explicit reconciliation after deletion.
`crig forget PATH` clears a local binding after confirmation, for example after a cancelled rental
or independently verified deletion. It does not stop billing or call the provider.
