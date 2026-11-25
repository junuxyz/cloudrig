"""Verify PIN encryption, /dev/tty gating, and file permissions in the key vault."""

import json
import os
import pty
import stat
import threading

import pytest

from cloudrig import vault
from cloudrig.models import Cancelled, CrigError


def test_encrypt_decrypt_roundtrip_and_wrong_pin():
    envelope = vault.encrypt_secret("vast-secret-action-token", "1234")
    assert "vast-secret-action-token" not in json.dumps(envelope)
    assert vault.decrypt_secret(envelope, "1234") == "vast-secret-action-token"

    with pytest.raises(CrigError, match="Wrong PIN"):
        vault.decrypt_secret(envelope, "9999")


def test_short_pin_is_rejected():
    with pytest.raises(CrigError, match="at least 4"):
        vault.encrypt_secret("secret", "123")


def test_save_and_unlock_keys_uses_private_permissions(monkeypatch):
    vault.save_keys("read-only-token", "action-secret-token", "4321", scoped=True)

    read_file = vault.read_key_path()
    action_file = vault.action_key_path()
    assert stat.S_IMODE(read_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(action_file.stat().st_mode) == 0o600
    assert "action-secret-token" not in action_file.read_text()
    assert vault.get_read_key() == "read-only-token"

    seen = {}

    def fake_tty(prompt, *, header=""):
        seen["prompt"] = prompt
        seen["header"] = header
        return "4321"

    monkeypatch.setattr(vault, "read_tty_secret", fake_tty)
    assert vault.unlock_action_key("DESTROY train-1") == "action-secret-token"
    assert "DESTROY train-1" in seen["header"]


def test_empty_pin_cancels_unlock(monkeypatch):
    vault.save_keys("read-tok", "action-tok", "4321")
    monkeypatch.setattr(vault, "read_tty_secret", lambda prompt, *, header="": "")
    with pytest.raises(Cancelled):
        vault.unlock_action_key("RENT gpu")


def test_missing_tty_blocks_noninteractive_unlock(monkeypatch):
    vault.save_keys("read-tok", "action-tok", "4321")

    def no_tty(path, flags):
        raise OSError("no controlling terminal")

    monkeypatch.setattr(os, "open", no_tty)
    with pytest.raises(CrigError, match="own terminal"):
        vault.unlock_action_key("RENT gpu")


def test_saving_unscoped_key_removes_stale_read_key():
    vault.save_keys("old-read", "old-action", "1111", scoped=True)
    assert vault.has_read_key()
    vault.save_keys(None, "new-only-action", "2222", scoped=False)
    assert not vault.has_read_key()
    assert vault.has_credentials()


def test_read_tty_secret_works_on_real_pty():
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    captured = bytearray()

    def writer():
        while b"PIN: " not in captured:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            captured.extend(chunk)
        os.write(master, b"secret-pin\n")

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        entered = vault.read_tty_secret("PIN: ", header="RENT 4090\n", tty_path=slave_name)
    finally:
        thread.join(timeout=2)
        os.close(slave)
        os.close(master)

    assert entered == "secret-pin"
    assert b"RENT 4090" in captured
    assert b"secret-pin" not in captured  # Echo stayed disabled.


def test_clear_keys_removes_both_files():
    vault.save_keys("r", "a", "1234")
    vault.clear_keys()
    assert not vault.has_read_key()
    assert not vault.has_credentials()
