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

    def fake_ready(ident, host, port, user, timeout):
        tried.append((host, port))
        return host == "ssh5.vast.ai"

    monkeypatch.setattr(connections, "ssh_ready", fake_ready)
    monkeypatch.setattr(connections, "open_code", lambda ident: opened.append(ident))

    connections.connect_editor(FakeAPI(), "777", timeout=5)
    assert attached == [("777", "ssh-ed25519 AAA")]
    assert tried == [("203.0.113.5", 22022), ("ssh5.vast.ai", 12345)]
    assert opened == ["777"]


def test_rejects_unsafe_ssh_host():
    with pytest.raises(CrigError, match="invalid SSH hostname"):
        connections.install_alias("777", "bad host; rm -rf /", 22)
