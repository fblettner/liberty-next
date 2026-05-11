from __future__ import annotations

from liberty.auth.password import hash_password, needs_rehash, verify_password


def test_hash_is_argon2id_and_salted() -> None:
    h1 = hash_password("hunter2")
    h2 = hash_password("hunter2")
    assert h1.startswith("$argon2id$")
    assert h1 != h2  # random salt


def test_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "correct horse battery staple") is True
    assert verify_password(h, "wrong") is False


def test_verify_handles_none_and_garbage() -> None:
    assert verify_password(None, "x") is False
    assert verify_password("", "x") is False
    assert verify_password("not-a-hash", "x") is False


def test_needs_rehash() -> None:
    assert needs_rehash(hash_password("x")) is False
    assert needs_rehash(None) is False
    assert needs_rehash("not-a-hash") is False
