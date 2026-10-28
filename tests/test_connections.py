import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

from cloudrig import broker, workspace
from cloudrig.connections import (
    install_alias,
    load_startup_script,
    open_code,
    run_startup,
    sync_command,
    upload_flags,
    wait_ready,
)
from cloudrig.demo import Demo
from cloudrig.models import CrigError, RateLimited
from cloudrig.project import Binding, Phase, Project, receipt


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


@pytest.fixture
def local_ssh(tmp_path, monkeypatch):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    real_ssh = shutil.which("ssh")
    transport = binaries / "ssh"
    transport.write_text(
        dedent(f"""\
        #!/usr/bin/env python3
        import os
        import sys

        arguments = sys.argv[1:]
        if "-G" in arguments:
            os.execv({real_ssh!r}, [{real_ssh!r}, *arguments])
        if arguments[:1] == ["-F"]:
            arguments = arguments[2:]
        os.execv("/bin/sh", ["sh", "-c", " ".join(arguments[1:])])
        """)
    )
    transport.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    return binaries


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
        sync_command(replace(grant, direct=False), "./", "/workspace", True)


@pytest.mark.parametrize("outcome", [0, 1, 77, "timeout"])
def test_startup_waits_for_remote_tools_and_reports_failures(grant, monkeypatch, outcome):
    local_run = subprocess.run
    remote = []

    def ssh_run(argv, **kwargs):
        if "-G" in argv:
            return local_run(argv, **kwargs)
        remote.append(argv[-1])
        if argv[-1] == "true":
            return subprocess.CompletedProcess(argv, 0)
        assert "bash -se -- 3.5.0" in argv[-1]
        assert kwargs["input"].startswith("#!/usr/bin/env bash\n")
        assert kwargs["timeout"] > 0
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="SECRET")
        return subprocess.CompletedProcess(argv, outcome, stdout="", stderr="SECRET")

    monkeypatch.setattr(subprocess, "run", ssh_run)
    provider = SimpleNamespace(instance=lambda _: grant)
    if outcome == 0:
        assert wait_ready(provider, grant.id) == grant
    else:
        with pytest.raises(CrigError) as error:
            wait_ready(provider, grant.id)
        assert "SECRET" not in str(error.value)
    assert remote[0] == "true" and len(remote) == 2


def test_rate_limited_preparation_waits_then_reuses_the_same_instance(grant, monkeypatch):
    calls = []
    sleeps = []
    progress = []
    monkeypatch.setattr("cloudrig.connections.time.sleep", sleeps.append)
    monkeypatch.setattr("cloudrig.connections.install_alias", lambda _: "existing-instance")
    monkeypatch.setattr("cloudrig.connections.run_startup", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0))

    def instance(identifier):
        calls.append(identifier)
        if len(calls) == 1:
            raise RateLimited(30)
        return grant

    assert wait_ready(SimpleNamespace(instance=instance), grant.id, progress=progress.append) == grant
    assert calls == [grant.id, grant.id]
    assert sleeps == [30]
    assert any("limiting requests" in message for message in progress)


def test_persistent_rate_limit_stops_at_preparation_deadline(monkeypatch):
    now = [0]
    monkeypatch.setattr("cloudrig.connections.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "cloudrig.connections.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )

    def instance(_):
        raise RateLimited(30)

    with pytest.raises(CrigError, match="remains rented"):
        wait_ready(SimpleNamespace(instance=instance), "existing-1", timeout=50)
    assert now[0] == 50


def test_custom_startup_uses_bash_and_rereads_edits(monkeypatch, tmp_path, isolated_home):
    source = tmp_path / "startup.sh"
    marker = isolated_home / "custom-startup"
    transport = tmp_path / "ssh"
    transport.write_text('#!/bin/sh\nexec /bin/sh -c "$2"\n')
    transport.chmod(0o755)
    monkeypatch.setattr(
        "cloudrig.connections.ssh_command", lambda name, command: [str(transport), name, command]
    )
    for value in ("first", "edited"):
        source.write_text(f'values=({value} bash)\nprintf "%s" "${{values[0]}}" > "$HOME/custom-startup"\n')
        run_startup("fixture", 10, script=load_startup_script(str(source)))
        assert marker.read_text() == value
    assert (isolated_home / ".cache/cloudrig/startup.log").exists()
    with pytest.raises(CrigError, match="startup script failed"):
        run_startup("fixture", 10, script='echo "custom error" >&2\nfalse\n')
    log = isolated_home / ".cache/cloudrig/startup.log"
    assert "custom error" in log.read_text()
    run_startup("fixture", 10, script="")
    assert "custom error" in log.read_text()


def test_real_rsync_copy_works_with_ssh_transport_and_secret_exclusions(grant, local_ssh, tmp_path):
    if not shutil.which("rsync"):
        pytest.skip("rsync missing")
    source = tmp_path / "source space"
    source.mkdir()
    (source / "hello.txt").write_text("round trip")
    (source / ".env").write_text("do not upload")
    (source / ".ssh").mkdir()
    (source / ".ssh" / "secret").write_text("do not upload")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (source / "unsafe-link").symlink_to(outside)
    remote = tmp_path / "remote space;$(touch SHOULD_NOT_EXIST)"
    remote.mkdir()
    destination = tmp_path / "download"
    destination.mkdir()
    upload = sync_command(grant, str(source) + "/", str(remote) + "/", True)
    subprocess.run(upload, capture_output=True, check=True, cwd=tmp_path)
    assert (remote / "hello.txt").read_text() == "round trip"
    assert not (remote / ".env").exists() and not (remote / ".ssh").exists()
    assert not (remote / "unsafe-link").is_symlink()
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()
    download = sync_command(grant, str(destination) + "/", str(remote) + "/", False)
    subprocess.run(download, capture_output=True, check=True, cwd=tmp_path)
    assert (destination / "hello.txt").read_text() == "round trip"


def test_upload_respects_git_negations_and_custom_exclusions_with_real_tools(tmp_path):
    source, target = tmp_path / "project", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / ".gitignore").write_text("*.bin\n!keep.bin\nsecret\\[1\\].txt\n")
    (source / ".crigignore").write_text("outputs/\n")
    for name in ("ignored.bin", "keep.bin", "main.py", "secrets.txt"):
        (source / name).write_text("synthetic")
    (source / "outputs").mkdir()
    (source / "outputs" / "result").write_text("synthetic")
    (source / "secret1.txt").write_text("synthetic")
    (source / "secret[1].txt").write_text("synthetic")
    flags = upload_flags(source)
    subprocess.run(["rsync", "-rl", *flags, "--", str(source) + "/", str(target)], check=True)
    assert (target / "keep.bin").exists() and (target / "main.py").exists()
    assert (target / "secret1.txt").exists()
    for name in ("ignored.bin", "secret[1].txt", "secrets.txt", "outputs", ".git"):
        assert not (target / name).exists()


def test_project_upload_and_uv_setup_complete_without_pytorch(grant, local_ssh, monkeypatch, tmp_path):
    class LocalProject(Project):
        @property
        def remote(self):
            return str(self.root.parent / "remote-project")

    source = tmp_path / "project"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "test-project"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
    )
    subprocess.run(["uv", "lock", "--offline"], cwd=source, check=True, capture_output=True)
    (source / "hello.py").write_text("print('uploaded')\n")
    (source / ".env").write_text("SYNTHETIC_SECRET=not-uploaded\n")
    binaries = local_ssh
    (binaries / "nvidia-smi").write_text("#!/bin/sh\necho 'GPU 0: synthetic GPU'\n")
    (binaries / "nvidia-smi").chmod(0o755)
    monkeypatch.setattr(workspace, "wait_ready", lambda *a, **k: grant)
    project = LocalProject(source, "test-project")
    result = workspace.prepare(
        project,
        None,
        Binding(Phase.PREPARING, id=grant.id),
        workspace.Options(setup=True, open_editor=False),
    )
    assert result.phase == Phase.READY and result.setup_complete
    assert receipt(project).id == grant.id
    remote = Path(project.remote)
    assert (remote / "hello.py").read_text() == "print('uploaded')\n"
    assert not (remote / ".env").exists()
    subprocess.run(
        [
            str(remote / ".venv/bin/python"),
            "-c",
            "import importlib.util; assert importlib.util.find_spec('torch') is None",
        ],
        check=True,
    )
