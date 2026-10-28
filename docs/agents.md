# Use Cloudrig with an agent

Give your agent this instruction:

> Install a verified Cloudrig wheel and check the service. Give me `crig` to run in my own terminal.
> Never request my password or API key, rent another GPU after an error, or stop or delete a GPU without my instruction.

The agent can install the ordinary CLI, inspect source, search offers, and list instances.
The user completes service installation, account login, and paid-operation authentication in their terminal.
Editor connections reuse approved SSH access. Additional approval needs fresh sudo.

Only GPU rental, VS Code connection, stop, and deletion are currently supported.
Project uploads, dependency setup, shell commands, file transfers, and remote-agent execution are deferred.
See [permissions](agent-isolation.md), [commands](guide.md), and [milestones](milestones.md).
