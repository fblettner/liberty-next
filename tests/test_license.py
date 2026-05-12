from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from liberty.licensing import LicenseResult, verify_license


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub_pem


def _make_token(priv, payload: dict, *, alg: str = "RS256") -> str:
    header = {"alg": alg, "typ": "JWT"}
    signing_input = f"{_b64u(json.dumps(header).encode())}.{_b64u(json.dumps(payload).encode())}".encode()
    sig = priv.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode() + "." + _b64u(sig)


# ── the no-key / malformed paths (use the embedded key — none of these reach signature checking) ──
@pytest.mark.parametrize("key", [None, "", "   ", "not-a-jwt", "a.b", "a.b.c.d"])
def test_no_or_malformed_key_is_restricted(key) -> None:
    r = verify_license(key)
    assert r.mode == "restricted" and not r.valid and r.error
    assert r.public_dict()["mode"] == "restricted"


def test_bad_signature_against_embedded_key(keypair) -> None:
    priv, _ = keypair
    token = _make_token(priv, {"customer": "Acme", "exp": int(time.time()) + 3600})
    r = verify_license(token)  # signed with a test key, verified with the *embedded* key → mismatch
    assert r.mode == "restricted" and r.error == "Invalid license key signature"


# ── the happy path + claim handling (verify against the matching test public key) ──
def test_valid_full_license(keypair) -> None:
    priv, pub = keypair
    exp = int(time.time()) + 86400
    token = _make_token(priv, {
        "customer": "Acme Corp", "email": "ops@acme.test", "plan": "enterprise",
        "apps": ["nomasx1", "nomajde"], "iat": int(time.time()), "exp": exp,
    })
    r = verify_license(token, public_key_pem=pub)
    assert r.valid and r.mode == "full"
    assert r.customer == "Acme Corp" and r.email == "ops@acme.test" and r.plan == "enterprise"
    assert r.apps == ["nomasx1", "nomajde"] and r.expires_at == exp
    assert r.covers("nomasx1") and r.covers("nomajde") and not r.covers("other")
    d = r.public_dict()
    assert d == {"mode": "full", "valid": True, "customer": "Acme Corp", "email": "ops@acme.test",
                 "plan": "enterprise", "apps": ["nomasx1", "nomajde"], "expires_at": exp}


def test_license_without_apps_covers_everything(keypair) -> None:
    priv, pub = keypair
    r = verify_license(_make_token(priv, {"customer": "Acme"}), public_key_pem=pub)  # no exp → no expiry
    assert r.valid and r.apps is None
    assert r.covers("nomasx1") and r.covers("anything")


def test_expired_license_is_restricted(keypair) -> None:
    priv, pub = keypair
    r = verify_license(_make_token(priv, {"customer": "Acme", "exp": int(time.time()) - 10}), public_key_pem=pub)
    assert r.mode == "restricted" and r.error == "License key expired"


def test_wrong_algorithm_is_restricted(keypair) -> None:
    priv, pub = keypair
    # an HS256 header (even though we still sign with RSA) → rejected before signature check
    token = _make_token(priv, {"customer": "Acme"}, alg="HS256")
    r = verify_license(token, public_key_pem=pub)
    assert r.mode == "restricted" and "algorithm" in (r.error or "").lower()


def test_restricted_result_covers_nothing() -> None:
    assert not LicenseResult().covers("nomasx1")
    assert not verify_license("").covers("anything")


# ── load_connectors gates `licensed = true` connectors ──
_CONNECTORS_TOML = """
[pools.default]
url = "sqlite+aiosqlite://"

[connectors.free]
type = "sql"
pool = "default"

[connectors.paid]
type = "sql"
pool = "default"
licensed = true
"""


def _write_connectors(tmp_path):
    p = tmp_path / "connectors.toml"
    p.write_text(_CONNECTORS_TOML)
    return p


def test_load_connectors_drops_licensed_without_a_license(tmp_path) -> None:
    from liberty.connectors import load_connectors
    reg = load_connectors(_write_connectors(tmp_path))  # no license → restricted
    assert set(reg.names()) == {"free"}


def test_load_connectors_keeps_licensed_when_covered(tmp_path, keypair) -> None:
    from liberty.connectors import load_connectors
    priv, pub = keypair
    lic = verify_license(_make_token(priv, {"customer": "Acme", "apps": ["paid"]}), public_key_pem=pub)
    reg = load_connectors(_write_connectors(tmp_path), license=lic)
    assert set(reg.names()) == {"free", "paid"}
    # a license that doesn't list "paid" → still dropped
    lic2 = verify_license(_make_token(priv, {"customer": "Acme", "apps": ["other"]}), public_key_pem=pub)
    reg2 = load_connectors(_write_connectors(tmp_path), license=lic2)
    assert set(reg2.names()) == {"free"}
