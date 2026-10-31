"""``[connectors] pools_path`` (a local file whose pools replace connectors.toml's) and the editors'
``${VAR}`` round-trip (an unchanged value saved back as its reference, not the value it resolved to)."""
from __future__ import annotations

import asyncio
import textwrap
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import (
    AISettings,
    AppSettings,
    AuthSettings,
    ChartSettings,
    ConnectorSettings,
    CryptoSettings,
    DashboardSettings,
    MenuSettings,
    NotifySettings,
    ScreenSettings,
    Settings,
    keep_env_refs,
)
from liberty.connectors import load_connectors
from liberty.connectors.config import PoolConfig, load_connectors_file
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "pools-local-test-secret"


# ── keep_env_refs ────────────────────────────────────────────────────────────────────────────

def test_keep_env_refs_unchanged_value_keeps_the_reference() -> None:
    env = {"PG_HOST": "db.local"}
    raw = {"url": "postgresql+asyncpg://u@${PG_HOST:-pg}:5432/x", "pool_size": 5}
    new = {"url": "postgresql+asyncpg://u@db.local:5432/x", "pool_size": 8}
    assert keep_env_refs(new, raw, env=env) == {"url": raw["url"], "pool_size": 8}


def test_keep_env_refs_default_branch_and_changed_value() -> None:
    raw = {"url": "postgresql+asyncpg://u@${PG_HOST:-pg}/x"}
    assert keep_env_refs({"url": "postgresql+asyncpg://u@pg/x"}, raw, env={})["url"] == raw["url"]
    # the user typed another host: written as given
    assert keep_env_refs({"url": "postgresql+asyncpg://u@other/x"}, raw, env={})["url"] == "postgresql+asyncpg://u@other/x"


def test_keep_env_refs_scalars_nested_and_new_keys() -> None:
    env = {"TLS": "false", "CO": "PS920"}
    raw = {"verify_ssl": "${TLS:-true}", "schemas": {"CO": "${CO}", "DD": "DD920"}}
    new = {"verify_ssl": False, "schemas": {"CO": "PS920", "DD": "DD920", "SY": "SY920"}, "extra": 1}
    assert keep_env_refs(new, raw, env=env) == {
        "verify_ssl": "${TLS:-true}", "schemas": {"CO": "${CO}", "DD": "DD920", "SY": "SY920"}, "extra": 1,
    }
    assert keep_env_refs({"verify_ssl": True}, raw, env=env) == {"verify_ssl": True}   # changed


# ── the loader ───────────────────────────────────────────────────────────────────────────────

def test_load_connectors_file_overlays_the_local_pools(tmp_path: Path) -> None:
    shared = tmp_path / "connectors.toml"
    shared.write_text(textwrap.dedent("""
        [pools.default]
        url = "postgresql+asyncpg://liberty@pg/liberty"
        [pools.jde]
        url = "oracle+oracledb://system@jde:1521/?service_name=JDE"
        [connectors.db]
        type = "sql"
        pool = "default"
    """))
    local = tmp_path / "pools.local.toml"
    local.write_text('[pools.default]\nurl = "postgresql+asyncpg://liberty@devbox/liberty"\npool_size = 2\n')
    cfg = load_connectors_file(shared, pools_path=local)
    assert cfg.pools["default"].url.endswith("@devbox/liberty") and cfg.pools["default"].pool_size == 2
    assert cfg.pools["jde"].url.startswith("oracle")            # not in the local file: the shared one
    assert set(cfg.connectors) == {"db"}
    # a missing local file (or none) = connectors.toml alone
    assert load_connectors_file(shared, pools_path=tmp_path / "nope.toml").pools["default"].url.endswith("@pg/liberty")
    reg = load_connectors(shared, pools_path=local)
    assert set(reg.pools.names()) == {"default", "jde"}


# ── the editors ──────────────────────────────────────────────────────────────────────────────

def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
        await pools.dispose()

    asyncio.run(go())


def _app(tmp_path: Path, conn_text: str, *, pools_path: Path | None = None):
    conn = tmp_path / "connectors.toml"
    conn.write_text(conn_text)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=conn, pools_path=pools_path),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
        dashboards=DashboardSettings(config_path=tmp_path / "dashboards.toml"),
        notify=NotifySettings(config_path=tmp_path / "notifications.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
        crypto=CryptoSettings(master_key="test-master-key-for-config-saves"),
    )
    return create_app(settings), conn


def _h(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"username": "admin", "password": "adminpw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def db_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LIB_TEST_DB_DIR", str(tmp_path))
    _seed(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    return tmp_path


_SHARED = textwrap.dedent("""
    [pools.default]
    url = "sqlite+aiosqlite:///${LIB_TEST_DB_DIR}/app.db"

    [connectors.db]
    type = "sql"
    pool = "default"

    [[connectors.db.queries]]
    name = "answer"
    sql = "SELECT 42 AS answer"
""")


def test_pools_save_keeps_env_references(db_dir: Path) -> None:
    app, conn = _app(db_dir, _SHARED)
    with TestClient(app) as client:
        h = _h(client)
        pools = client.get("/admin/config/pools", headers=h).json()["pools"]
        assert pools["default"]["url"] == f"sqlite+aiosqlite:///{db_dir}/app.db"     # resolved for the editor
        pools["default"]["pool_size"] = 7
        assert client.put("/admin/config/pools", json={"pools": pools}, headers=h).json()["saved"] is True
    saved = tomllib.loads(conn.read_text())["pools"]["default"]
    assert saved["url"] == "sqlite+aiosqlite:///${LIB_TEST_DB_DIR}/app.db" and saved["pool_size"] == 7


def test_pools_path_takes_the_saves_and_the_runtime(db_dir: Path) -> None:
    local = db_dir / "pools.local.toml"
    app, conn = _app(db_dir, _SHARED, pools_path=local)
    before = conn.read_text()
    with TestClient(app) as client:
        h = _h(client)
        body = client.get("/admin/config/pools", headers=h).json()
        assert body["path"] == str(local)
        dev_url = f"sqlite+aiosqlite:///{db_dir}/app.db"
        new = {"default": {"url": dev_url}, "cache": {"url": "sqlite+aiosqlite://", "password": "s3cret"}}
        assert client.put("/admin/config/pools", json={"pools": new}, headers=h).json()["path"] == str(local)
        assert conn.read_text() == before                                  # the shared file untouched
        written = tomllib.loads(local.read_text())["pools"]
        assert written["cache"]["password"].startswith("ENC:") and set(written) == {"default", "cache"}
        assert set(client.post("/admin/reload", headers=h).json()["pools"]) == {"default", "cache"}
        assert client.get("/api/sql/db/answer", headers=h).json()["rows"] == [{"answer": 42}]


def test_connectors_save_keeps_env_references_and_doesnt_encrypt_them(db_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIB_TEST_AIS_URL", "https://ais.example")
    monkeypatch.setenv("LIB_TEST_AIS_PW", "pw")
    text = _SHARED + textwrap.dedent("""
        [connectors.ais]
        type = "api"
        base_url = "${LIB_TEST_AIS_URL}"
        auth_type = "basic"
        auth_username = "JDE"
        auth_password = "${LIB_TEST_AIS_PW}"
    """)
    app, conn = _app(db_dir, text)
    with TestClient(app) as client:
        h = _h(client)
        conns = client.get("/admin/config/connectors/parsed", headers=h).json()["connectors"]
        assert conns["ais"]["base_url"] == "https://ais.example"
        conns["ais"]["auth_username"] = "JDE2"
        assert client.put("/admin/config/connectors/parsed", json={"connectors": conns}, headers=h).json()["saved"] is True
    ais = tomllib.loads(conn.read_text())["connectors"]["ais"]
    assert ais["base_url"] == "${LIB_TEST_AIS_URL}" and ais["auth_password"] == "${LIB_TEST_AIS_PW}"
    assert ais["auth_username"] == "JDE2"
    # the pools ride along untouched
    assert tomllib.loads(conn.read_text())["pools"]["default"]["url"] == "sqlite+aiosqlite:///${LIB_TEST_DB_DIR}/app.db"
