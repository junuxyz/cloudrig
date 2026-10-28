# Milestones

## Agent access controls

Planned for a later release. Not implemented or scheduled.

- Require fresh sudo authentication for GPU stop operations, alongside the existing checks for rent, start, and delete.
- Restrict agents to approved `crig` operations, with no direct access to the SSH signer or connection.
- Define which remote operations agents may run. Unrestricted `exec` and SSH access cannot enforce command limits.
- Verify that agents cannot bypass these restrictions before describing them as security guarantees.

Current behavior is documented in [agent access limitations](agent-isolation.md#limitations).
