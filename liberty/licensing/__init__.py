"""License-key verification — RS256-signed JWTs, checked against an embedded public key.

The open framework is free; *licensed* connectors (e.g. ``nomasx1`` / ``nomajde``, configured
with ``licensed = true``) are unlocked by a license key — an RS256 JWT the vendor signs with the
matching private key (a separate key-gen tool; the public half ships here as ``public.pem``).
This module only *verifies*: a valid signature + a not-past ``exp`` → ``mode = "full"`` and the
claims; anything else → ``mode = "restricted"`` (the licensed connectors are then not loaded).

Ported from nomaubl's ``custom.ubl.license.LicenseVerifier``; same JWT shape and the same key.
No external JWT library — :mod:`cryptography` (already a dep) does the RSA verify.

Claims used: ``customer`` / ``email`` / ``plan`` (informational), ``apps`` (optional list of
connector names this key covers — absent ⇒ covers every ``licensed`` connector), ``exp`` (epoch
seconds; absent ⇒ no expiry), ``iat`` (ignored).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

_log = logging.getLogger(__name__)

_PUBLIC_KEY_PATH = Path(__file__).with_name("public.pem")
_cached_key: RSAPublicKey | None = None

# Connectors that are ALWAYS licensed — operators can't bypass this via the
# Settings checkbox or by hand-editing connectors.toml. The loader gates them
# even when their entry sets ``licensed = false`` on disk (see
# :func:`liberty.connectors.registry.load_connectors`); the admin PUT endpoint
# rewrites ``licensed = true`` for these names on save. Add a name here when a
# new vendor-licensed connector ships.
ALWAYS_LICENSED_CONNECTORS: frozenset[str] = frozenset({"nomasx1", "nomajde"})


@dataclass(frozen=True, slots=True)
class LicenseResult:
    """Outcome of :func:`verify_license`.

    ``mode`` is ``"full"`` (a valid, unexpired key) or ``"restricted"`` (no key / invalid /
    expired). ``apps`` is the list of connector names the key covers (``None`` ⇒ all licensed
    connectors). ``expires_at`` is epoch seconds (``None`` ⇒ no expiry).
    """

    mode: str = "restricted"
    customer: str | None = None
    email: str | None = None
    plan: str | None = None
    apps: list[str] | None = None
    expires_at: int | None = None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.mode == "full"

    def covers(self, connector_name: str) -> bool:
        """Whether a ``licensed`` connector named *connector_name* is unlocked by this key
        (valid, and either no ``apps`` claim or this name is in it)."""
        return self.valid and (self.apps is None or connector_name in self.apps)

    def public_dict(self) -> dict[str, Any]:
        """The license status as a wire-safe dict (no key material) — for ``GET /api/license``."""
        d: dict[str, Any] = {"mode": self.mode, "valid": self.valid}
        for k in ("customer", "email", "plan", "error"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.apps is not None:
            d["apps"] = self.apps
        if self.expires_at is not None:
            d["expires_at"] = self.expires_at
        return d


def _restricted(error: str) -> LicenseResult:
    return LicenseResult(mode="restricted", error=error)


def _load_public_key(pem: str | bytes | None = None) -> RSAPublicKey:
    """Load the verification key — *pem* if given (for tests), else the embedded ``public.pem`` (cached)."""
    global _cached_key
    if pem is not None:
        data = pem.encode() if isinstance(pem, str) else pem
        key = serialization.load_pem_public_key(data)
        if not isinstance(key, RSAPublicKey):
            raise ValueError("license public key is not RSA")
        return key
    if _cached_key is None:
        key = serialization.load_pem_public_key(_PUBLIC_KEY_PATH.read_bytes())
        if not isinstance(key, RSAPublicKey):
            raise ValueError("embedded license public key is not RSA")
        _cached_key = key
    return _cached_key


def _b64url(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def verify_license(
    key: str | None,
    *,
    public_key_pem: str | bytes | None = None,
    master_key: str = "",
) -> LicenseResult:
    """Verify an RS256 JWT license *key*. Returns a :class:`LicenseResult` — never raises.

    *public_key_pem* overrides the embedded key (testing only). An empty/blank key →
    ``restricted`` (the normal "running the open framework" state).

    *master_key*: when the stored key is ENC:-encrypted (Settings UI save encrypts
    with the install's master key), decrypt it before verification. An empty
    master_key + ENC value → restricted with a clear error."""
    if not key or not key.strip():
        return _restricted("No license key configured")
    if key.startswith("ENC:"):
        if not master_key:
            return _restricted("License key is encrypted but no master_key configured")
        try:
            from liberty.crypto import decrypt
            key = decrypt(key, master_key)
        except Exception as exc:  # noqa: BLE001 — surface decrypt failure as a license error
            return _restricted(f"License key decryption failed: {exc}")
    try:
        parts = key.strip().split(".")
        if len(parts) != 3:
            return _restricted("Invalid license key format")
        header_b, payload_b, sig_b = parts

        header = json.loads(_b64url(header_b))
        if str(header.get("alg", "")).upper() != "RS256":
            return _restricted(f"Unsupported license key algorithm: {header.get('alg')!r}")

        try:
            pub = _load_public_key(public_key_pem)
        except Exception as exc:  # noqa: BLE001 — bad/missing key material
            _log.warning("license: cannot load public key — %s: %s", type(exc).__name__, exc)
            return _restricted("License public key unavailable")

        signed = f"{header_b}.{payload_b}".encode()
        try:
            pub.verify(_b64url(sig_b), signed, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return _restricted("Invalid license key signature")

        payload = json.loads(_b64url(payload_b))
        exp = payload.get("exp")
        exp_i = int(exp) if isinstance(exp, (int, float)) else None
        if exp_i is not None and exp_i < time.time():
            return _restricted("License key expired")

        apps_raw = payload.get("apps")
        apps = [str(a) for a in apps_raw] if isinstance(apps_raw, list) else None
        return LicenseResult(
            mode="full",
            customer=_str_or_none(payload.get("customer")),
            email=_str_or_none(payload.get("email")),
            plan=_str_or_none(payload.get("plan")),
            apps=apps,
            expires_at=exp_i,
        )
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        return _restricted(f"Malformed license key: {exc}")
    except Exception as exc:  # noqa: BLE001 — never let a license problem crash startup
        _log.warning("license: verification error — %s: %s", type(exc).__name__, exc)
        return _restricted(f"License verification error: {exc}")


def _str_or_none(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


__all__ = ["ALWAYS_LICENSED_CONNECTORS", "LicenseResult", "verify_license"]
