import os
import socket
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from cloudrig import broker
from cloudrig.connections import connect_editor, install_alias, open_code
from cloudrig.demo import Demo
from cloudrig.models import CrigError


@pytest.fixture
def grant(isolated_home, monkeypatch):
    temporary = tempfile.TemporaryDirectory(prefix="cg-grant-", dir="/tmp")
    public_root = Path(temporary.name)
    public = public_root / "identity.pub"
    public.write_text("ssh-ed25519 AAAATEST public-only")
    known = public_root / "known_hosts"
    known.write_text("vastai-demo-01 ssh-ed25519 AAAATEST")
    endpoint = public_root / "agent.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))
    monkeypatch.setattr(broker, "PUBLIC_ROOT", public_root)
    monkeypatch.setattr(broker, "ROOT_UID", os.getuid())
    monkeypatch.setattr(broker, "trusted_root_path", lambda _: None)
    item = replace(
        Demo().instances()[0],
        approved=True,
        public_key=str(public),
        known_hosts=str(known),
        agent_socket=str(endpoint),
    )
    yield item
    listener.close()
    temporary.cleanup()


def test_generated_config_uses_public_key_and_restricted_signer(grant, isolated_home):
    ssh = isolated_home / ".ssh"
    ssh.mkdir(mode=0o700)
    original = "Host personal\n  HostName example.org\n  ForwardAgent yes\n"
    (ssh / "config").write_text(original)
    name = install_alias(grant)
    install_alias(replace(grant, host="203.0.113.90", port=3333))
    contents = (ssh / "cloudrig" / "config").read_text()
    assert contents.count(f"Host {name}\n") == 1
    assert "identity.pub" in contents and "IdentityAgent" in contents
    assert "StrictHostKeyChecking yes" in contents
    assert "ForwardAgent no" in contents
    assert original in (ssh / "config").read_text()
    result = subprocess.run(
        ["ssh", "-G", "-F", str(ssh / "cloudrig" / "config"), name],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "hostname 203.0.113.90" in result.stdout
    assert "forwardagent no" in result.stdout
    assert ".pub" in next(line for line in result.stdout.splitlines() if line.startswith("identityfile "))


def test_editor_uses_native_ssh_without_key_content(grant, monkeypatch):
    calls = []
    monkeypatch.setattr("cloudrig.connections.run", calls.append)
    open_code(grant, "/workspace/a b;$(evil)")
    assert calls == [["code", "--remote", "ssh-remote+crig-vastai-demo-01", "/workspace/a b;$(evil)"]]


def test_wrong_grant_path_or_proxy_only_connection_is_rejected(grant):
    with pytest.raises(CrigError):
        install_alias(replace(grant, public_key="/tmp/untrusted.pub"))
    with pytest.raises(CrigError):
        open_code(replace(grant, direct=False), "/workspace")


def test_editor_waits_through_rate_limit_on_the_existing_gpu(grant, monkeypatch):
    from types import SimpleNamespace

    from cloudrig.models import RateLimited

    calls, sleeps, opened = [], [], []
    monkeypatch.setattr("cloudrig.connections.require", lambda name: name)
    monkeypatch.setattr("cloudrig.connections.time.sleep", sleeps.append)
    monkeypatch.setattr("cloudrig.connections.open_code", lambda item, path: opened.append((item.id, path)))

    def instance(identifier):
        calls.append(identifier)
        if len(calls) == 1:
            raise RateLimited(30)
        return grant

    connect_editor(SimpleNamespace(instance=instance), grant.id)
    assert calls == [grant.id, grant.id]
    assert sleeps == [30]
    assert opened == [(grant.id, "/workspace")]
