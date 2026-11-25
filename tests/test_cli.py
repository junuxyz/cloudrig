"""Test CLI commands, login flow, lazy TUI import, and demo mode."""

import json
import sys

import pytest

from cloudrig import cli, vast, vault
from cloudrig.models import CrigError


def test_cli_demo_json_and_plain(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["crig", "--gpu", "4090", "--json", "--demo"])
    cli.main()
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["gpu"] == "RTX 4090"

    monkeypatch.setattr(sys, "argv", ["crig", "--rented", "--json", "--demo"])
    cli.main()
    rented = json.loads(capsys.readouterr().out)
    assert len(rented) == 3


def test_login_mints_scoped_keys_and_discards_master(monkeypatch):
    inputs = iter(["master-vast-key", "1234", "1234"])
    monkeypatch.setattr(vault, "read_tty_secret", lambda prompt, *, header="": next(inputs))
    monkeypatch.setattr(vast, "verify_key", lambda token: None)
    monkeypatch.setattr(vast, "mint_scoped_keys", lambda token: ("scoped-read", "scoped-action"))

    monkeypatch.setattr(sys, "argv", ["crig", "login"])
    cli.main()

    assert vault.get_read_key() == "scoped-read"
    assert "master-vast-key" not in vault.read_key_path().read_text()
    assert "master-vast-key" not in vault.action_key_path().read_text()
    assert "scoped-action" not in vault.action_key_path().read_text()


def test_login_falls_back_when_key_cannot_mint_subkeys(monkeypatch):
    inputs = iter(["prescoped-key", "4321", "4321"])
    monkeypatch.setattr(vault, "read_tty_secret", lambda prompt, *, header="": next(inputs))
    monkeypatch.setattr(vast, "verify_key", lambda token: None)

    def denied(token):
        raise vast._AuthDenied("no user_write")

    monkeypatch.setattr(vast, "mint_scoped_keys", denied)

    monkeypatch.setattr(sys, "argv", ["crig", "login"])
    cli.main()

    assert not vault.has_read_key()
    assert vault.has_credentials()
    assert "prescoped-key" not in vault.action_key_path().read_text()


def test_login_rejects_mismatched_pin(monkeypatch):
    inputs = iter(["master-key", "1234", "9999"])
    monkeypatch.setattr(vault, "read_tty_secret", lambda prompt, *, header="": next(inputs))
    with pytest.raises(CrigError, match="did not match"):
        cli.login()
    assert not vault.has_credentials()


def test_logout_clears_saved_keys(monkeypatch):
    vault.save_keys("r", "a", "1234")
    monkeypatch.setattr(sys, "argv", ["crig", "logout"])
    cli.main()
    assert not vault.has_credentials()
    assert not vault.has_read_key()


def test_noninteractive_without_credentials_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["crig", "--rented", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "crig login" in capsys.readouterr().out
