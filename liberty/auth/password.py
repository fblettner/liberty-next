"""Password hashing — Argon2id via ``argon2-cffi`` (v1 used a weaker scheme).

A single module-level :class:`~argon2.PasswordHasher` with library defaults
(Argon2id, sensible time/memory cost). :func:`verify_password` also reports
whether the stored hash should be upgraded so callers can transparently rehash
on successful login.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return an Argon2id hash string for *plain*."""
    return _hasher.hash(plain)


def verify_password(stored_hash: str | None, plain: str) -> bool:
    """Return ``True`` iff *plain* matches *stored_hash*. ``None``/garbage → ``False``."""
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str | None) -> bool:
    """Return ``True`` if *stored_hash* uses outdated parameters and should be re-hashed."""
    if not stored_hash:
        return False
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False
