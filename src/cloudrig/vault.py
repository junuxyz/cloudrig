"""Store the Vast.ai read key and PIN-encrypted action key."""

import base64
import hashlib
import json
import os
import termios
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cloudrig.models import Cancelled, CrigError
from cloudrig.state import atomic_write, config_dir, state_lock

_AAD = b"cloudrig-action-key-v1"
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_MIN_PIN_LEN = 4


def read_key_path() -> Path:
    return config_dir() / "read.key"


def action_key_path() -> Path:
    return config_dir() / "action.key.enc"


def _derive_key(
    pin: str, salt: bytes, *, n: int = _SCRYPT_N, r: int = _SCRYPT_R, p: int = _SCRYPT_P
) -> bytes:
    return hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)


def encrypt_secret(secret: str, pin: str) -> dict:
    if len(pin) < _MIN_PIN_LEN:
        raise CrigError(f"Use a PIN or passphrase with at least {_MIN_PIN_LEN} characters.")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(pin, salt)
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), _AAD)
    return {
        "v": 1,
        "kdf": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_secret(envelope: dict, pin: str) -> str:
    try:
        if envelope.get("v") != 1 or envelope.get("kdf") != "scrypt":
            raise ValueError("Unsupported vault format")
        n, r, p = int(envelope["n"]), int(envelope["r"]), int(envelope["p"])
        if not (2**12 <= n <= 2**18 and 1 <= r <= 16 and 1 <= p <= 4):
            raise ValueError("Invalid key derivation parameters")
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ct"], validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise CrigError("Saved key file is damaged. Run crig login to set it up again.") from error

    key = _derive_key(pin, salt, n=n, r=r, p=p)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag:
        raise CrigError("Wrong PIN.") from None
    return plaintext.decode("utf-8")


def read_tty_secret(prompt: str, *, header: str = "") -> str:
    """Read a secret directly from /dev/tty so piped stdin or non-TTY tools cannot bypass it."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as error:
        raise CrigError("Run this command in your own terminal to confirm with your PIN.") from error

    try:
        with os.fdopen(fd, "r+", encoding="utf-8", buffering=1) as tty:
            if header:
                tty.write(header)
                tty.flush()
            tty.write(prompt)
            tty.flush()
            saved = termios.tcgetattr(tty.fileno())
            hidden = list(saved)
            hidden[3] = hidden[3] & ~termios.ECHO
            try:
                termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, hidden)
                line = tty.readline()
            finally:
                termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, saved)
                tty.write("\n")
                tty.flush()
    except (OSError, termios.error) as error:
        raise CrigError("Terminal input is unavailable. Run this command in your own terminal.") from error

    if not line:
        raise Cancelled("Cancelled.")
    return line.rstrip("\r\n")


def has_credentials() -> bool:
    return action_key_path().is_file()


def has_read_key() -> bool:
    return read_key_path().is_file()


def get_read_key() -> str:
    path = read_key_path()
    if not path.is_file():
        raise CrigError("Not logged in. Run crig login in your terminal.")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise CrigError("Saved read key is empty. Run crig login.")
    return key


def save_keys(read_key: str | None, action_key: str, pin: str, *, scoped: bool = True) -> None:
    envelope = encrypt_secret(action_key.strip(), pin)
    envelope["scoped"] = scoped
    with state_lock():
        atomic_write(action_key_path(), json.dumps(envelope, indent=2) + "\n", mode=0o600)
        if read_key:
            atomic_write(read_key_path(), read_key.strip() + "\n", mode=0o600)
        else:
            read_key_path().unlink(missing_ok=True)


def unlock_action_key(summary: str) -> str:
    path = action_key_path()
    if not path.is_file():
        raise CrigError("Not logged in. Run crig login in your terminal.")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CrigError("Saved key file is damaged. Run crig login.") from error

    pin = read_tty_secret("PIN (empty to cancel): ", header=f"\n{summary}\n\n")
    if not pin:
        raise Cancelled("Cancelled.")
    return decrypt_secret(envelope, pin)


def clear_keys() -> None:
    with state_lock():
        read_key_path().unlink(missing_ok=True)
        action_key_path().unlink(missing_ok=True)
