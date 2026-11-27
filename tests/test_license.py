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


# ── one application or another: what a key that covers only some of them loads and offers ──
_SUITE_TOML = """
[pools.default]
url = "sqlite+aiosqlite://"

[connectors.free]
type = "sql"
pool = "default"

[connectors.audit]
type = "sql"
pool = "default"
licensed = true
applications = ["admin"]      # admin reads its data: it stays loaded for it

[connectors.admin]
type = "sql"
pool = "default"
licensed = true

[connectors.admin_services]
type = "sql"
pool = "default"
applications = ["admin"]      # a part of admin: it follows it
"""


def _suite(tmp_path, keypair, apps):
    from liberty.connectors import load_connectors
    priv, pub = keypair
    p = tmp_path / "connectors.toml"
    p.write_text(_SUITE_TOML)
    lic = verify_license(_make_token(priv, {"customer": "Acme", "plan": "x", **({} if apps is None else {"apps": apps})}), public_key_pem=pub)
    return load_connectors(p, license=lic)


def test_a_key_for_one_application_loads_its_parts_and_what_it_reads(tmp_path, keypair) -> None:
    from liberty.licensing import offered

    # admin only: its services follow it; audit stays loaded for its data, but is not offered
    reg = _suite(tmp_path, keypair, ["admin"])
    assert set(reg.names()) == {"free", "admin", "admin_services", "audit"}
    assert reg.unlicensed >= {"audit"} and "admin" not in reg.unlicensed
    assert offered(reg, "admin") and offered(reg, "free") and not offered(reg, "audit")
    # audit only: admin and its services are left out
    reg = _suite(tmp_path, keypair, ["audit"])
    assert set(reg.names()) == {"free", "audit"} and reg.gated == {"admin", "admin_services"}
    assert offered(reg, "audit") and not offered(reg, "admin")
    # both, or a key that names no application: everything
    for apps in (["audit", "admin"], None):
        reg = _suite(tmp_path, keypair, apps)
        assert set(reg.names()) == {"free", "admin", "admin_services", "audit"} and not ({"audit", "admin"} & reg.unlicensed)


def test_without_a_key_a_part_of_a_licensed_application_is_not_loaded(tmp_path) -> None:
    from liberty.connectors import load_connectors
    p = tmp_path / "connectors.toml"
    p.write_text(_SUITE_TOML)
    reg = load_connectors(p)
    assert set(reg.names()) == {"free"} and {"audit", "admin"} <= reg.unlicensed


def test_a_job_follows_the_applications_it_names(tmp_path) -> None:
    from liberty.jobs import load_jobs
    p = tmp_path / "jobs.toml"
    step = '[[jobs.steps]]\ntype = "sql_query"\nname = "s"\nconnector = "free"\nquery = "q"\n'
    p.write_text("".join(f'[[jobs]]\nid = "{jid}"\n{apps}{step}\n' for jid, apps in (
        ("alone", ""), ("audit-only", 'applications = ["audit"]\n'), ("shared", 'applications = ["audit", "admin"]\n'))))
    assert {j.id for j in load_jobs(p).jobs()} == {"alone", "audit-only", "shared"}
    assert {j.id for j in load_jobs(p, unlicensed=frozenset({"audit"})).jobs()} == {"alone", "shared"}
    assert {j.id for j in load_jobs(p, unlicensed=frozenset({"audit", "admin"})).jobs()} == {"alone"}


def test_a_menu_shows_nothing_of_an_application_the_key_does_not_cover() -> None:
    from types import SimpleNamespace

    from liberty.auth.principal import Principal
    from liberty.dashboards.config import parse_dashboards
    from liberty.menus import parse_menus
    from liberty.web.menus import _keeper

    menu = parse_menus({"menus": {"admin": {"label": "Admin", "items": [
        {"id": "own", "label": "Own page", "type": "page", "target": "/app/admin/tasks"},
        {"id": "theirs", "label": "A page of audit", "type": "page", "target": "/app/audit/sod?x=1"},
        {"id": "svc", "label": "Service", "type": "endpoint", "connector": "left_out", "target": "status"},
    ]}}}).menus["admin"]
    who = Principal(id="1", username="u", permissions=("*",), is_superuser=True)

    def seen(unlicensed, gated=()):
        reg = SimpleNamespace(unlicensed=frozenset(unlicensed), gated=frozenset(gated))
        keep = _keeper(who, parse_dashboards({}), "admin", charts=None, explorer=None, connectors=reg)
        return [it.id for it in menu.items if keep(it, it.connector or "admin")]

    assert seen(()) == ["own", "theirs", "svc"]
    assert seen({"audit"}, {"left_out"}) == ["own"]          # another application's page, a connector left out
    assert seen({"admin"}) == []                               # the application itself
