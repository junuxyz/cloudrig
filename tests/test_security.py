import json
import subprocess
import sys
from dataclasses import replace

import pytest

from cloudrig.connections import check_remote_rsync, check_rsync, install_alias, run, sync_command
from cloudrig.demo import Demo
from cloudrig.models import CrigError


def test_child_tools_never_receive_secrets_or_signing_socket(monkeypatch, tmp_path):
    names = [
        "VAST_API_KEY",
        "VASTAI_API_KEY",
        "RUNPOD_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "HF_TOKEN",
        "OTHER_SECRET",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "BASH_ENV",
        "RSYNC_RSH",
        "SSH_ASKPASS",
        "PYTHONPATH",
        "LD_PRELOAD",
    ]
    for name in names:
        monkeypatch.setenv(name, "test-marker")
    output = tmp_path / "environment.json"
    run(
        [
            sys.executable,
            "-c",
            "import os,json,sys;open(sys.argv[1],'w').write(json.dumps(list(os.environ)))",
            str(output),
        ]
    )
    visible = json.loads(output.read_text())
    assert all(name not in visible for name in names)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_bad_prices_never_reach_checkout(value):
    with pytest.raises(CrigError):
        replace(Demo().offers("5090")[0], hourly=value)


@pytest.mark.parametrize("value", ["GPU\x1b]52;c;DATA\x07", "GPU\n$0.00", "GPU\u202eexe"])
def test_terminal_metadata_injection_rejected(value):
    with pytest.raises(CrigError):
        replace(Demo().instances()[0], name=value)


@pytest.mark.parametrize("value", ["../other", "id/other", "id?query=evil", "id\nHost *"])
def test_resource_alias_injection_rejected(value):
    with pytest.raises(CrigError):
        replace(Demo().instances()[0], id=value)


def test_ordinary_instance_without_root_grant_cannot_connect():
    with pytest.raises(CrigError, match="approve"):
        install_alias(Demo().instances()[0])


@pytest.mark.parametrize("host", ["fe80::1%zone\nMatch exec evil", "fe80::1%eth0", "host\nProxyCommand evil"])
def test_host_address_cannot_inject_ssh_config(host):
    with pytest.raises(CrigError):
        install_alias(replace(Demo().instances()[0], host=host))


@pytest.mark.parametrize("local", ["~", "~/.ssh/", "~/.aws/credentials", "~/.zshrc", "/"])
def test_transfer_refuses_host_credential_paths(local):
    for upload in [True, False]:
        with pytest.raises(CrigError, match="credential"):
            sync_command(Demo().instances()[0], None, local, "/workspace/", upload)


def test_outdated_rsync_is_refused(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="rsync version 3.4.4\nSECRET", stderr=""),
    )
    for check in [check_rsync, lambda: check_remote_rsync("alias")]:
        with pytest.raises(CrigError, match="3.5.0") as error:
            check()
        assert "SECRET" not in str(error.value)


def test_total_quote_overflow_is_refused():
    offer = replace(Demo().offers("5090")[0], disk_hourly_gb=1e308)
    with pytest.raises(CrigError):
        offer.total(40)


def test_unexpected_errors_do_not_dump_credentials(monkeypatch, capsys):
    from cloudrig.cli import main

    monkeypatch.setattr(sys, "argv", ["crig", "--demo", "--rented"])

    def fail(_):
        raise RuntimeError("PRIVATE_TOKEN")

    monkeypatch.setattr("cloudrig.cli.dispatch", fail)
    with pytest.raises(SystemExit):
        main()
    assert "PRIVATE_TOKEN" not in capsys.readouterr().out
