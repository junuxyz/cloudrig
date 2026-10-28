# Start here

Install the wheel matching your platform from [Releases](https://github.com/junuxyz/cloudrig/releases):

```sh
uv tool install ./cloudrig-*.whl
crig
```

Requires uv, Python 3.11+, and macOS 13+ or Linux with systemd. Release wheels include the protected
service; no Go installation or source path is needed. For source builds, see [Contributing](../CONTRIBUTING.md).

## First use

1. Choose Vast.ai or RunPod. Create an account and add credit on its website.
2. Approve protected service installation in your own terminal.
3. Enter an API key. The login screen links to its provider's key page.
4. Open GPU offers, select a GPU, review its price, and approve the rental with sudo.
5. In Instances, press Enter to open VS Code, stop the GPU, or delete it.

Editor connections require VS Code with Remote - SSH, the `code` command on PATH, and system
OpenSSH 8.9+. You can rent and manage GPUs without an editor. VS Code opens `/workspace` on the GPU.
Project files and dependencies are not uploaded or installed automatically.

Closing the dashboard or editor does not stop the GPU. Stop it when finished; storage may still
cost money. Deleting removes instance-local files.

## Updates

The dashboard checks for a newer stable release every ten days. Run `crig update` to download and
verify it. GitHub CLI is required for signature verification. Your API keys and rental records stay.

See [commands](guide.md), [agent instructions](agents.md), and [security](security.md).
