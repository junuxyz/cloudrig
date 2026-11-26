"""Test per-instance SSH keypair management, config blocks, and cleanup."""

from pathlib import Path

import pytest

from cloudrig import connections
from cloudrig.models import CrigError, Instance


def test_keypair_alias_and_cleanup_lifecycle():
    private, pub = connections.ensure_keypair("9001")
    assert private.is_file()
    assert pub.startswith("ssh-ed25519 ")

    name = connections.install_alias("9001", "203.0.113.10", 22022)
    assert name == "crig-9001"

    managed_config = (Path.home() / ".ssh" / "cloudrig" / "config").read_text()
    assert "Host crig-9001" in managed_config
    assert "HostName 203.0.113.10" in managed_config
    assert "ForwardAgent no" in managed_config
    assert "IdentitiesOnly yes" in managed_config

    main_config = (Path.home() / ".ssh" / "config").read_text()
    assert f'Include "{Path.home() / ".ssh" / "cloudrig" / "config"}"' in main_config

    connections.cleanup_instance("9001")
    assert not connections.has_keypair("9001")
    assert "Host crig-9001" not in (Path.home() / ".ssh" / "cloudrig" / "config").read_text()


def test_connect_editor_falls_back_to_proxy_and_attaches_key(monkeypatch):
    attached = []
    opened = []
    tried = []

    class FakeAPI:
        def instance(self, ident):
            return Instance(
                id=ident,
                provider="vastai",
                name="train",
                gpu="RTX 4090",
                count=1,
                status="running",
                hourly=0.3,
                region="US",
                host="203.0.113.5",
                port=22022,
                direct=True,
                proxy_host="ssh5.vast.ai",
                proxy_port=12345,
            )

        def attach_ssh_key(self, ident, pub):
            attached.append((ident, pub))

    monkeypatch.setattr(connections, "require", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(connections, "ensure_keypair", lambda ident: (Path("/tmp/k"), "ssh-ed25519 AAA"))

    def fake_probe(ident, host, port, user, timeout, setup):
        tried.append((host, port))
        return 0 if host == "ssh5.vast.ai" else 1

    monkeypatch.setattr(connections, "editor_setup_command", lambda: "")
    monkeypatch.setattr(connections, "_probe_ssh", fake_probe)
    monkeypatch.setattr(connections, "open_code", lambda ident: opened.append(ident))

    connections.connect_editor(FakeAPI(), "777", timeout=5)
    assert attached == [("777", "ssh-ed25519 AAA")]
    assert tried == [("203.0.113.5", 22022), ("ssh5.vast.ai", 12345)]
    assert opened == ["777"]


def test_rejects_unsafe_ssh_host():
    with pytest.raises(CrigError, match="invalid SSH hostname"):
        connections.install_alias("777", "bad host; rm -rf /", 22)


def test_try_connect_editor_now_reports_loading_status(monkeypatch):
    class LoadingAPI:
        def instance(self, ident):
            return Instance(ident, "vastai", "train", "RTX 5090", 1, "loading", 0.4, "US")

    monkeypatch.setattr(connections, "require", lambda binary: f"/usr/bin/{binary}")
    opened, status = connections.try_connect_editor_now(LoadingAPI(), LoadingAPI().instance("777"))
    assert opened is False
    assert status == "GPU is loading"


def test_connect_editor_honors_cancel_event(monkeypatch):
    import threading

    class LoadingAPI:
        def instance(self, ident):
            return Instance(ident, "vastai", "train", "RTX 5090", 1, "loading", 0.4, "US")

    monkeypatch.setattr(connections, "require", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(connections, "has_keypair", lambda ident: True)
    cancel = threading.Event()
    cancel.set()
    assert connections.connect_editor(LoadingAPI(), "777", timeout=30, cancel=cancel) is False


def test_ssh_probe_creates_the_workspace_vs_code_opens(monkeypatch):
    commands = []

    class Done:
        returncode = 0
        stderr = ""

    connections.ensure_keypair("9002")
    monkeypatch.setattr(connections, "require", lambda binary: binary)
    monkeypatch.setattr(connections, "_tcp_ssh_banner", lambda host, port, timeout: True)
    monkeypatch.setattr(connections.subprocess, "run", lambda args, **kw: commands.append(args) or Done())
    assert connections.ssh_ready("9002", "203.0.113.10", 22022, "root", 3.0)
    assert f"mkdir -p {connections.WORKSPACE}" in commands[0][-1]
    assert f"mktemp {connections.WORKSPACE}/" in commands[0][-1]
    connections.cleanup_instance("9002")


@pytest.mark.parametrize("wait_in_background", [True, False])
def test_both_editor_commands_wait_for_preparation(monkeypatch, wait_in_background):
    item = Instance("777", "vastai", "gpu", "GPU", 1, "running", 0.1, "US", host="gpu.test", port=22)

    class API:
        def instance(self, ident):
            return item

    monkeypatch.setattr(connections, "require", lambda name: name)
    monkeypatch.setattr(connections, "has_keypair", lambda ident: True)
    monkeypatch.setattr(connections, "editor_setup_command", lambda: "setup")
    statuses = iter([75, 0])
    monkeypatch.setattr(connections, "_probe_ssh", lambda *args: next(statuses))
    monkeypatch.setattr(connections, "_wait_or_cancel", lambda *args: False)
    monkeypatch.setattr(connections, "install_alias", lambda *args: None)
    opened = []
    monkeypatch.setattr(connections, "open_code", opened.append)

    if wait_in_background:
        messages = []
        assert connections.connect_editor(API(), item.id, progress=messages.append)
        assert "Preparing VS Code Server" in messages
    else:
        assert connections.try_connect_editor_now(API(), item) == (False, "Preparing VS Code Server")
        assert not opened
        assert connections.try_connect_editor_now(API(), item) == (True, "opened")
    assert opened == [item.id]
