from __future__ import annotations

import asyncio
import os
import sys

import pytest

from liberty.auth.authstore import (
    AuthError,
    AuthFile,
    TomlAuthBackend,
    build_auth_backend,
    load_auth,
)
from liberty.config import AuthSettings, Settings


@pytest.fixture
def backend(tmp_path) -> TomlAuthBackend:
    return TomlAuthBackend(tmp_path / "auth.toml")


def _run(coro):
    return asyncio.run(coro)


def test_ready_creates_empty_file_and_chmods(backend: TomlAuthBackend) -> None:
    assert not backend.path.exists()
    _run(backend.ready())
    assert backend.path.exists() and load_auth(backend.path) == AuthFile()
    if sys.platform != "win32":
        assert oct(os.stat(backend.path).st_mode & 0o777) == "0o600"


def test_create_role_and_user_then_authenticate(backend: TomlAuthBackend) -> None:
    _run(backend.get_or_create_role("admin", permissions=["*"], description="all"))
    _run(backend.get_or_create_role("reader", permissions=["sql:liberty:read"]))
    u = _run(backend.create_user("alice", password="alicepw", email="a@x.test", roles=["reader"]))
    assert u.username == "alice" and u.email == "a@x.test" and u.roles == ["reader"]
    assert u.permissions == ["sql:liberty:read"] and not u.is_superuser and u.provider == "local"
    # round-trips through the file; the password is hashed (not plaintext)
    f = load_auth(backend.path)
    assert "alice" in f.users and f.users["alice"].password_hash not in (None, "alicepw")
    assert f.roles["admin"].permissions == ["*"]

    assert _run(backend.authenticate("alice", "alicepw")) is not None
    assert _run(backend.authenticate("alice", "wrong")) is None
    assert _run(backend.authenticate("nobody", "x")) is None
    assert _run(backend.get_by_id("alice")).username == "alice"   # by-id == by-username for the TOML backend
    assert _run(backend.get_by_id("nobody")) is None
    assert _run(backend.count_users()) == 1
    assert {n for n, *_ in _run(backend.list_roles())} == {"admin", "reader"}
    assert [r.username for r in _run(backend.list_users())] == ["alice"]


def test_superuser_flag_resolves_permissions(backend: TomlAuthBackend) -> None:
    _run(backend.get_or_create_role("admin", permissions=["*"]))
    su = _run(backend.create_user("root", password="x", is_superuser=True, roles=["admin"]))
    assert su.is_superuser and su.permissions == ["*"]


def test_inactive_user_cannot_authenticate(backend: TomlAuthBackend) -> None:
    _run(backend.create_user("bob", password="bobpw", is_active=False))
    assert _run(backend.authenticate("bob", "bobpw")) is None
    _run(backend.set_active("bob", True))
    assert _run(backend.authenticate("bob", "bobpw")) is not None
    _run(backend.set_active("bob", False))
    assert _run(backend.authenticate("bob", "bobpw")) is None


def test_set_password_and_assign_roles(backend: TomlAuthBackend) -> None:
    _run(backend.get_or_create_role("r1", permissions=["a:b"]))
    _run(backend.get_or_create_role("r2", permissions=["c:d"]))
    _run(backend.create_user("u", password="old"))
    _run(backend.set_password("u", "new"))
    assert _run(backend.authenticate("u", "old")) is None
    assert _run(backend.authenticate("u", "new")) is not None
    assert _run(backend.assign_roles("u", ["r1"])).roles == ["r1"]
    rec = _run(backend.assign_roles("u", ["r2"]))                  # add (not replace)
    assert rec.roles == ["r1", "r2"] and set(rec.permissions) == {"a:b", "c:d"}
    rec = _run(backend.assign_roles("u", ["r1"], replace=True))    # replace
    assert rec.roles == ["r1"] and rec.permissions == ["a:b"]


def test_errors(backend: TomlAuthBackend) -> None:
    _run(backend.create_user("dup", password="x"))
    with pytest.raises(AuthError, match="already exists"):
        _run(backend.create_user("dup", password="y"))
    with pytest.raises(AuthError, match="unknown role"):
        _run(backend.create_user("nope", password="x", roles=["missing"]))
    with pytest.raises(AuthError, match="unknown user"):
        _run(backend.set_password("ghost", "x"))
    with pytest.raises(AuthError, match="unknown user"):
        _run(backend.set_active("ghost", False))


def test_provision_oidc_user(backend: TomlAuthBackend) -> None:
    _run(backend.get_or_create_role("viewer", permissions=["sql:*"]))
    u1 = _run(backend.provision_oidc_user(
        {"sub": "abc123", "email": "jane@corp.test", "name": "Jane"},
        email_claim="email", name_claim="name", default_roles=["viewer"],
    ))
    assert u1.provider == "oidc" and u1.provider_subject == "abc123" and u1.email == "jane@corp.test"
    assert u1.password_hash is None and u1.roles == ["viewer"]
    # same sub again → same account, profile refreshed, no duplicate
    u2 = _run(backend.provision_oidc_user({"sub": "abc123", "email": "jane2@corp.test", "name": "Jane R."}))
    assert u2.username == u1.username and u2.email == "jane2@corp.test"
    assert len(load_auth(backend.path).users) == 1
    # a different sub whose preferred_username collides with u1's username → suffixed
    u3 = _run(backend.provision_oidc_user({"sub": "different", "preferred_username": u1.username}))
    assert u3.username.startswith(u1.username) and u3.username != u1.username and u3.provider_subject == "different"
    with pytest.raises(AuthError, match="missing 'sub'"):
        _run(backend.provision_oidc_user({"email": "no-sub@x.test"}))


def test_build_auth_backend_picks_by_settings(tmp_path) -> None:
    assert isinstance(build_auth_backend(Settings(auth=AuthSettings(backend="toml", toml_path=tmp_path / "a.toml")), None), TomlAuthBackend)


def test_load_auth_missing_is_empty(tmp_path) -> None:
    assert load_auth(tmp_path / "does-not-exist.toml") == AuthFile()


def test_app_login_with_toml_backend(tmp_path) -> None:
    """End-to-end: create_app with backend="toml" → /auth/login works, no DB touched."""
    from fastapi.testclient import TestClient
    from liberty.config import AppSettings, ConnectorSettings
    from liberty.main import create_app

    auth_toml = tmp_path / "auth.toml"
    be = TomlAuthBackend(auth_toml)
    _run(be.get_or_create_role("admin", permissions=["*"]))
    _run(be.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"]))

    app = create_app(Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=tmp_path / "no-connectors.toml"),  # missing → empty registry, no DB
        auth=AuthSettings(backend="toml", toml_path=auth_toml, jwt_secret="authstore-test-secret"),
    ))
    with TestClient(app) as client:
        r = client.post("/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200
        tok = r.json()
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {tok['access_token']}"})
        assert me.status_code == 200 and me.json()["username"] == "admin" and me.json()["is_superuser"] is True
        # refresh re-reads the (TOML) user — subject is the username here
        r2 = client.post("/auth/refresh", json={"refresh_token": tok["refresh_token"]})
        assert r2.status_code == 200 and "access_token" in r2.json()
        # bad creds → 401
        assert client.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
