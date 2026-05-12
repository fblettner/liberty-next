from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from liberty.crypto import PREFIX, CryptoError, decrypt, decrypt_if_needed, decrypt_or_keep, encrypt, is_encrypted

MK = "v1-compat-test-master-key"  # arbitrary — the PBKDF2 key derivation works for any string


def _v1_encrypt(text: str, master_key: str) -> str:
    """An independent reimplementation of liberty v1's Encryption.encrypt_text — used to
    prove liberty.crypto.decrypt reads what v1 wrote (and vice versa)."""
    iv, salt = os.urandom(16), os.urandom(64)
    key = PBKDF2HMAC(algorithm=hashes.SHA512(), length=32, salt=salt, iterations=2145).derive(master_key.encode("utf-8"))
    enc = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    ct = enc.update(text.encode("utf-8")) + enc.finalize()
    return "ENC:" + base64.b64encode(salt + iv + enc.tag + ct).decode("utf-8")


def _v1_decrypt(value: str, master_key: str) -> str:
    blob = base64.b64decode(value[len("ENC:"):])
    salt, iv, tag, ct = blob[:64], blob[64:80], blob[80:96], blob[96:]
    key = PBKDF2HMAC(algorithm=hashes.SHA512(), length=32, salt=salt, iterations=2145).derive(master_key.encode("utf-8"))
    dec = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
    return (dec.update(ct) + dec.finalize()).decode("utf-8")


def test_round_trip() -> None:
    enc = encrypt("hunter2 — accents é ✓", MK)
    assert enc.startswith(PREFIX) and is_encrypted(enc)
    assert decrypt(enc, MK) == "hunter2 — accents é ✓"
    # randomised: two encryptions of the same text differ
    assert encrypt("x", MK) != encrypt("x", MK)


def test_decrypt_tolerates_trailing_whitespace() -> None:
    # v1 decodes base64 leniently; an ENC: value copied with a stray newline (DB column, file, …)
    # must still decrypt here — only the base64 part is lenient; a leading-whitespace `ENC:` is still
    # rejected by `is_encrypted` (the prefix must be at the start), which matches v1.
    enc = encrypt("a JDE password", MK)
    assert decrypt(enc + "\n", MK) == "a JDE password"
    assert decrypt(enc + "  \n\t", MK) == "a JDE password"


def test_format_layout() -> None:
    blob = base64.b64decode(encrypt("abc", MK)[len(PREFIX):])
    assert len(blob) == 64 + 16 + 16 + 3  # salt + iv + tag + len("abc")


def test_idempotent_encrypt() -> None:
    enc = encrypt("x", MK)
    assert encrypt(enc, MK) == enc  # already ENC: → returned unchanged
    assert encrypt(enc, "") == enc  # ...even without a key


def test_decrypt_non_encrypted_raises() -> None:
    with pytest.raises(CryptoError, match="not encrypted"):
        decrypt("plain text", MK)


def test_missing_key_raises() -> None:
    with pytest.raises(CryptoError, match="no master key"):
        encrypt("x", "")
    with pytest.raises(CryptoError, match="no master key"):
        decrypt(encrypt("x", MK), "")


def test_wrong_key_raises() -> None:
    with pytest.raises(CryptoError, match="decryption failed"):
        decrypt(encrypt("x", "key-one"), "key-two")
    with pytest.raises(CryptoError, match="decryption failed"):
        decrypt("ENC:not-valid-base64-or-blob", MK)


def test_v1_compatibility() -> None:
    # v2 reads what v1 wrote
    assert decrypt(_v1_encrypt("a v1 secret", MK), MK) == "a v1 secret"
    # ...and v1 reads what v2 writes
    assert _v1_decrypt(encrypt("a v2 secret", MK), MK) == "a v2 secret"
    # works with a non-default master key too
    assert decrypt(_v1_encrypt("z", "my-custom-key"), "my-custom-key") == "z"


def test_decrypt_if_needed_and_or_keep() -> None:
    enc = encrypt("s", MK)
    assert decrypt_if_needed("plain", MK) == "plain"
    assert decrypt_if_needed(enc, MK) == "s"
    assert decrypt_or_keep("plain", "anything") == ("plain", None)
    assert decrypt_or_keep(enc, MK) == ("s", None)
    kept, err = decrypt_or_keep(enc, "wrong-key")
    assert kept == enc and err and "decryption failed" in err
