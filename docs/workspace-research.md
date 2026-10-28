# Workspace design choices

Cloudrig binds a local directory to one provider instance and remote folder. Reconnection preserves
remote edits. An uncertain rental response keeps its request reserved until the user checks the
provider account. A setup error never authorizes another rental.

The local CLI handles files and editor commands. A separate root helper holds provider credentials
and constrains new spending and SSH approval. This requires a service installation and helper-specific
sudo settings. It provides different protection from keeping an API key readable by the CLI;
it also makes onboarding harder. See [security.md](security.md) for the exact limits.

## Related tools

These projects cover related workflows. Their linked documentation describes their own capabilities;
Cloudrig has no measured speed or reliability comparison against them.

- [Official Vast CLI](https://github.com/vast-ai/vast-cli)
- [Vast SSH and editor setup](https://docs.vast.ai/guides/instances/connect/ssh)
- [Vastly](https://github.com/seamus-fallows/vastly)
- [NVIDIA Brev](https://docs.nvidia.com/brev/latest/cli/instance-management)
- [SkyPilot](https://docs.skypilot.ai/en/latest/examples/interactive-development.html)
- [RunPod Pod commands](https://docs.runpod.io/runpodctl/reference/pod)

## Current limits

Cloudrig has no automatic shutdown, conflict-aware output merge, checkpoint recovery on a new GPU,
or independent provider attestation of the first SSH host key. Its dependency setup detects uv and
pip requirements files. Other dependency managers need a custom script or manual setup.
New RunPod project workspaces remain unavailable until complete storage quotes are supported.

[Project workspaces](project-workspaces.md) describes the supported flow.
