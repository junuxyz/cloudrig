# Milestones

The current release focuses on renting GPUs, opening VS Code, stopping, and deleting.
Account setup, key protection, request limits, and safe rental records remain part of that flow.

## Preserved implementation

The full implementation is saved on the local `deferred-project-workflows` branch at
`71b9cfc626b9aed8020936dad9e3da8ddf89fda2`. It includes the removed code, tests, documentation,
and previews. The simplified version is on `gpu-rental-core`.

Bring features back in small changes after checking their behavior against the current core.

## Later releases

| Milestone | Scope | Before adding it back |
| --- | --- | --- |
| Project workspaces | `init`, `plan`, `up`, `down`, saved project settings, upload preview, dependency setup. | Verify retries preserve remote changes and reuse one allocation. |
| Advanced remote tools | Shell access, remote commands, push/pull, startup scripts, Claude Code and Codex launchers. | Keep credentials protected and report failures without creating another GPU. |
| Background project preparation | Workers, preparation history, notifications, and detailed progress views. | Verify cancellation, restart, rate limits, and editor handoff. |
| Resume stopped GPUs | Start existing instances. | Recheck the current price and require fresh authentication. |
| Agent access controls | Restrict agents to approved operations and prevent direct SSH use. | Verify enforcement before making a security guarantee. |

These milestones are not scheduled. See [current limitations](agent-isolation.md#limitations).
