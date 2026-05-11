"""Field-level encryption, byte-compatible with Liberty v1's ``Encryption``.

v1 stores secrets in DB columns (``SETTINGS_APPLICATIONS.password``,
``ly_api_conn.conn_password``, …) as ``ENC:`` + base64(``salt[64] | iv[16] | tag[16] |
ciphertext``), AES-256-GCM with the key derived via PBKDF2-HMAC-SHA512 (2145 iterations,
32-byte key) from a ``MASTER_KEY`` string. v2 reuses the *same* scheme and key so values
v1 wrote are readable here, values v2 writes are readable by v1 (and by any other tool
that uses the same ``MASTER_KEY``) — re-encrypting an existing database is not required.

Set the key via ``[crypto] master_key`` in ``config/app.toml`` / ``LIBERTY_MASTER_KEY``.
It must equal v1's ``MASTER_KEY`` (the value in v1's ``secrets.json``) — always supply it
through the environment, never hard-code it in a config file or the source.

Standalone usage from another script::

    from liberty.crypto import encrypt, decrypt
    decrypt("ENC:…", os.environ["LIBERTY_MASTER_KEY"])
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher as _AesGcm, algorithms as _alg, modes as _modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PREFIX = "ENC:"
_SALT_LEN, _IV_LEN, _TAG_LEN = 64, 16, 16
_ITERATIONS, _KEY_LEN = 2145, 32

_NO_KEY = "no master key configured — set [crypto] master_key / LIBERTY_MASTER_KEY (must match v1's MASTER_KEY)"


class CryptoError(Exception):
    """Encryption/decryption failure (no key configured, malformed input, wrong key, tampered)."""


def is_encrypted(value: object) -> bool:
    """True if *value* is a string carrying the ``ENC:`` marker."""
    return isinstance(value, str) and value.startswith(PREFIX)


def _key(master_key: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA512(), length=_KEY_LEN, salt=salt, iterations=_ITERATIONS).derive(
        master_key.encode("utf-8")
    )


def encrypt(text: str, master_key: str) -> str:
    """Encrypt *text* → ``ENC:…``. Idempotent — an already-``ENC:`` value is returned unchanged."""
    if is_encrypted(text):
        return text
    if not master_key:
        raise CryptoError(_NO_KEY)
    salt, iv = os.urandom(_SALT_LEN), os.urandom(_IV_LEN)
    enc = _AesGcm(_alg.AES(_key(master_key, salt)), _modes.GCM(iv)).encryptor()
    ct = enc.update(text.encode("utf-8")) + enc.finalize()
    return PREFIX + base64.b64encode(salt + iv + enc.tag + ct).decode("ascii")


def decrypt(value: str, master_key: str) -> str:
    """Decrypt an ``ENC:…`` value. Raises :class:`CryptoError` on a non-``ENC:`` value,
    a missing key, or a key/integrity mismatch."""
    if not is_encrypted(value):
        raise CryptoError("value is not encrypted (no 'ENC:' prefix)")
    if not master_key:
        raise CryptoError(_NO_KEY)
    try:
        blob = base64.b64decode(value[len(PREFIX) :], validate=True)
        salt = blob[:_SALT_LEN]
        iv = blob[_SALT_LEN : _SALT_LEN + _IV_LEN]
        tag = blob[_SALT_LEN + _IV_LEN : _SALT_LEN + _IV_LEN + _TAG_LEN]
        ct = blob[_SALT_LEN + _IV_LEN + _TAG_LEN :]
        dec = _AesGcm(_alg.AES(_key(master_key, salt)), _modes.GCM(iv, tag)).decryptor()
        return (dec.update(ct) + dec.finalize()).decode("utf-8")
    except CryptoError:
        raise
    except Exception as exc:  # bad base64, wrong key (GCM tag mismatch), tampered, …
        raise CryptoError(f"decryption failed: {type(exc).__name__}: {exc}") from exc


def decrypt_if_needed(value: str, master_key: str) -> str:
    """Decrypt *value* iff it's ``ENC:``-prefixed; otherwise return it unchanged."""
    return decrypt(value, master_key) if is_encrypted(value) else value


def decrypt_or_keep(value: str, master_key: str) -> tuple[str, str | None]:
    """Like :func:`decrypt_if_needed` but never raises — returns ``(value_or_decrypted, error)``;
    ``error`` is a message when an ``ENC:`` value couldn't be decrypted (kept as-is)."""
    if not is_encrypted(value):
        return value, None
    try:
        return decrypt(value, master_key), None
    except CryptoError as exc:
        return value, str(exc)
