from __future__ import annotations

import asyncio
import json

import pytest

from liberty.admin_cli import main
from liberty.auth.db import AuthDatabase
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    app_toml = tmp_path / "app.toml"
    app_toml.write_text('[auth]\nbackend = "db"\npool = "default"\n')
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "{db_url}"\n')
    base = ["--config-app", str(app_toml), "--config-connectors", str(conn_toml)]
    return base, db_url


def _run(base, *args, capsys):
    rc = main([*base, *args])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def _authenticate(db_url: str, username: str, password: str):
    async def go():
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        try:
            async with db.session() as s:
                from liberty.auth.service import AuthService

                return await AuthService(s).authenticate(username, password)
        finally:
            await pools.dispose()

    return asyncio.run(go())


def test_init_db_bootstraps_admin(env, capsys) -> None:
    base, db_url = env
    rc, out = _run(base, "init-db", "--admin-username", "root", capsys=capsys)
    assert rc == 0
    assert out["ready"] is True and out["backend"] == "db" and out["admin_created"] is True
    assert out["user"]["username"] == "root"
    assert out["user"]["is_superuser"] is True
    assert out["user"]["roles"] == ["admin"]
    password = out["generated_password"]

    user = _authenticate(db_url, "root", password)
    assert user is not None and user.is_superuser and user.role_names == ["admin"]


def test_init_db_is_idempotent(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", capsys=capsys)
    rc, out = _run(base, "init-db", capsys=capsys)
    assert rc == 0 and out["admin_created"] is False


def test_init_db_honours_explicit_password(env, capsys) -> None:
    base, db_url = env
    rc, out = _run(base, "init-db", "--password", "chosen-pw", capsys=capsys)
    assert rc == 0
    assert "generated_password" not in out
    assert _authenticate(db_url, "admin", "chosen-pw") is not None


def test_init_db_password_from_env(env, capsys, monkeypatch) -> None:
    base, db_url = env
    monkeypatch.setenv("MY_ADMIN_PW", "from-env-pw")
    rc, _ = _run(base, "init-db", "--password-env", "MY_ADMIN_PW", capsys=capsys)
    assert rc == 0
    assert _authenticate(db_url, "admin", "from-env-pw") is not None


# --------------------------------------------------------------------------- #
# _seed_default_pool — stale-master-key detection (regression for the
# "--reset wipes named volumes but /apps bind mount survives → old ENC: password
# can't decrypt with the new install's master_key" install failure).
# --------------------------------------------------------------------------- #


def _seed_ctx(tmp_path, master_key: str, connectors_text: str):
    """Build a minimal _Context wired against connectors.toml on disk.

    Pin ``[connectors] config_path`` in app.toml — ``_seed_default_pool`` reads
    that, not whatever override _Context() got via --config-connectors, so they
    must agree (otherwise the seed writes elsewhere than the registry reads)."""
    from liberty.admin_cli import _Context
    import argparse
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(connectors_text)
    app_toml = tmp_path / "app.toml"
    app_toml.write_text(
        '[auth]\nbackend = "db"\npool = "default"\n'
        f'[crypto]\nmaster_key = "{master_key}"\n'
        f'[connectors]\nconfig_path = "{conn_toml}"\n'
    )
    args = argparse.Namespace(
        config_app=str(app_toml), config_connectors=str(conn_toml),
    )
    return _Context(args), conn_toml


def test_seed_default_pool_reseeds_when_encpw_is_stale(tmp_path, monkeypatch) -> None:
    """The headline regression: /apps survives --reset, the previous install's
    ENC: password is still on disk encrypted with the OLD master key, the new
    install can't decrypt → asyncpg gets the literal ENC:... and rejects auth.
    The seed must detect this and re-seed with a fresh password."""
    from liberty.admin_cli import _seed_default_pool
    from liberty.crypto import decrypt, encrypt

    old_key = "OLD-master-key-3zTvzr3p67VC61jmV54rIYu1545x4TlY"
    new_key = "NEW-master-key-99999999999999999999999999999999"
    stale_pw = encrypt("the-real-pg-password", old_key)

    conn_text = (
        '[pools.default]\n'
        'url = "postgresql+asyncpg://liberty@pg:5432/liberty"\n'
        f'password = "{stale_pw}"\n'
        'dialect = "postgresql"\n'
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "fresh-pw-from-this-install")
    monkeypatch.setenv("POSTGRES_USER", "liberty")
    monkeypatch.setenv("POSTGRES_HOST", "pg")
    monkeypatch.setenv("POSTGRES_DB", "liberty")
    ctx, conn_toml = _seed_ctx(tmp_path, master_key=new_key, connectors_text=conn_text)
    try:
        seeded = asyncio.run(_seed_default_pool(ctx))
    finally:
        asyncio.run(ctx.aclose())

    assert seeded is True, "should re-seed when the existing ENC: password won't decrypt"
    # Verify the new password on disk decrypts cleanly with the NEW key.
    import tomllib
    doc = tomllib.loads(conn_toml.read_text())
    new_pw = doc["pools"]["default"]["password"]
    assert new_pw.startswith("ENC:")
    assert decrypt(new_pw, new_key) == "fresh-pw-from-this-install"


def test_seed_default_pool_skips_when_encpw_decrypts(tmp_path, monkeypatch) -> None:
    """The healthy case: an existing [pools.default] whose ENC: password decrypts
    cleanly with the current master key is left alone (idempotent re-run)."""
    from liberty.admin_cli import _seed_default_pool
    from liberty.crypto import encrypt

    key = "matching-master-key-555555555555555555555555555"
    pw_ciphertext = encrypt("real-pg-password", key)
    conn_text = (
        '[pools.default]\n'
        'url = "postgresql+asyncpg://liberty@pg:5432/liberty"\n'
        f'password = "{pw_ciphertext}"\n'
        'dialect = "postgresql"\n'
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "should-not-be-written")
    ctx, conn_toml = _seed_ctx(tmp_path, master_key=key, connectors_text=conn_text)
    try:
        seeded = asyncio.run(_seed_default_pool(ctx))
    finally:
        asyncio.run(ctx.aclose())

    assert seeded is False
    # File untouched — same ciphertext, same URL.
    assert pw_ciphertext in conn_toml.read_text()


def test_create_user_and_login(env, capsys) -> None:
    base, db_url = env
    _run(base, "init-db", capsys=capsys)
    rc, out = _run(base, "create-user", "alice", "--password", "alicepw", "--email", "a@x.test", capsys=capsys)
    assert rc == 0 and out["user"]["username"] == "alice" and out["user"]["email"] == "a@x.test"
    assert _authenticate(db_url, "alice", "alicepw") is not None


def test_create_user_with_role(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", capsys=capsys)
    _run(base, "create-role", "reader", "--permission", "sql:liberty:read", capsys=capsys)
    rc, out = _run(base, "create-user", "carol", "--password", "p", "--role", "reader", capsys=capsys)
    assert rc == 0 and out["user"]["roles"] == ["reader"]
    assert out["user"]["permissions"] == ["sql:liberty:read"]


def test_create_user_generates_password(env, capsys) -> None:
    base, db_url = env
    _run(base, "init-db", capsys=capsys)
    rc, out = _run(base, "create-user", "dave", capsys=capsys)
    assert rc == 0 and "generated_password" in out
    assert _authenticate(db_url, "dave", out["generated_password"]) is not None


def test_duplicate_user_returns_error_code(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", "--admin-username", "root", capsys=capsys)
    assert main([*base, "create-user", "root", "--password", "x"]) == 2


def test_set_password(env, capsys) -> None:
    base, db_url = env
    _run(base, "init-db", capsys=capsys)
    _run(base, "create-user", "ed", "--password", "old", capsys=capsys)
    rc, out = _run(base, "set-password", "ed", "--password", "new", capsys=capsys)
    assert rc == 0 and out["password_changed"] is True
    assert _authenticate(db_url, "ed", "old") is None
    assert _authenticate(db_url, "ed", "new") is not None


def test_set_password_unknown_user(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", capsys=capsys)
    assert main([*base, "set-password", "nobody", "--password", "x"]) == 2  # AuthError → exit 2


def test_set_active_disables_login(env, capsys) -> None:
    base, db_url = env
    _run(base, "init-db", capsys=capsys)
    _run(base, "create-user", "fred", "--password", "fredpw", capsys=capsys)
    assert _authenticate(db_url, "fred", "fredpw") is not None
    rc, _ = _run(base, "set-active", "fred", "--inactive", capsys=capsys)
    assert rc == 0
    assert _authenticate(db_url, "fred", "fredpw") is None


def test_list_users(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", "--admin-username", "root", capsys=capsys)
    _run(base, "create-user", "alice", "--password", "p", capsys=capsys)
    rc, out = _run(base, "list-users", capsys=capsys)
    assert rc == 0
    assert {u["username"] for u in out} == {"root", "alice"}


def test_create_role(env, capsys) -> None:
    base, _ = env
    _run(base, "init-db", capsys=capsys)
    rc, out = _run(base, "create-role", "ops", "--permission", "a:b:c", "--permission", "x:*", "--description", "ops", capsys=capsys)
    assert rc == 0
    assert out["role"]["name"] == "ops"
    assert out["role"]["permissions"] == ["a:b:c", "x:*"]
    assert out["role"]["description"] == "ops"
