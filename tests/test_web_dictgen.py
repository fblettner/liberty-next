"""``POST /admin/dictionary/scan`` — column → dictionary-item proposals (superuser)."""
from __future__ import annotations

import asyncio
import sqlite3
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AppSettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-dictgen-test-secret"


def _seed(db_url: str, db_path: str) -> None:
    # a domain table to scan, on the same pool the auth store uses
    c = sqlite3.connect(db_path)
    c.execute("CREATE TABLE customers (cust_id INTEGER, cust_name TEXT, created_at DATE, is_active INTEGER)")
    c.commit(); c.close()

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("alice", password="alicepw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / "app.db")
    db_url = f"sqlite+aiosqlite:///{db_path}"
    (tmp_path / "connectors.toml").write_text(textwrap.dedent(f"""
        [pools.default]
        url = "{db_url}"
        [connectors.app1]
        type = "sql"
        pool = "default"
        [[connectors.app1.queries]]
        name = "x_get"
        sql = "SELECT 1 AS id"
        [[connectors.app1.queries]]
        name = "customers_get"
        sql = "SELECT cust_id, cust_name, created_at, is_active FROM customers"
    """))
    # an existing dictionary entry CUST_ID (scoped to app1) — so the scan flags it as existing
    (tmp_path / "dictionary.toml").write_text('[connectors.app1.entries.CUST_ID]\nlabel = "Customer"\n')
    _seed(db_url, db_path)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(tmp_path / "connectors.toml")),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
    )
    return create_app(settings)


def _h(client: TestClient, username: str) -> dict[str, str]:
    tok = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_requires_superuser(app) -> None:
    with TestClient(app) as client:
        assert client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "customers"}).status_code == 401
        assert client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "customers"}, headers=_h(client, "alice")).status_code == 403


def test_scan_infers_format_and_flags_existing(app) -> None:
    with TestClient(app) as client:
        r = client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "customers"}, headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["jde"] is False and body["dialect"] == "sqlite"
        by = {it["column"]: it for it in body["items"]}
        # dd ids are uppercased to the convention (find_entry is case-sensitive; editors uppercase too)
        assert by["cust_id"]["dd_id"] == "CUST_ID" and by["cust_name"]["dd_id"] == "CUST_NAME"
        assert by["cust_id"]["exists"] is True                 # matched the existing CUST_ID entry
        assert by["cust_id"]["format"] == "number"
        assert by["cust_name"]["exists"] is False and by["cust_name"]["format"] is None
        assert by["created_at"]["format"] == "date"
        assert by["is_active"]["format"] == "number"
        # non-JDE → no data item, source inferred
        assert all(it["data_item"] is None and it["source"] == "inferred" for it in body["items"])


def test_scan_jde_forced_strips_prefix(app) -> None:
    # Force JDE on (no real JDE DD in the test → labels blank, but the data-item strip must happen).
    with TestClient(app) as client:
        body = client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "customers", "jde": True}, headers=_h(client, "admin")).json()
        assert body["jde"] is True
        by = {it["column"]: it for it in body["items"]}
        assert by["cust_id"]["data_item"] == "ST_ID"       # 'cust_id' minus the 2-char prefix 'cu' → 'ST_ID'
        assert by["cust_name"]["data_item"] == "ST_NAME"


def test_scan_by_query_describes_reversed_table(app) -> None:
    """Standalone path: scan an already-reversed read query's result columns (no DB table walk)."""
    with TestClient(app) as client:
        r = client.post("/admin/dictionary/scan", json={"connector": "app1", "query": "customers_get"}, headers=_h(client, "admin"))
        assert r.status_code == 200
        by = {it["column"]: it for it in r.json()["items"]}
        assert set(by) == {"cust_id", "cust_name", "created_at", "is_active"}
        assert by["cust_id"]["exists"] is True            # matches the existing CUST_ID entry
        assert by["cust_name"]["exists"] is False


def test_scan_requires_exactly_one_of_table_or_query(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.post("/admin/dictionary/scan", json={"connector": "app1"}, headers=h).status_code == 400
        assert client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "customers", "query": "customers_get"}, headers=h).status_code == 400


def test_scan_unknown_table_404(app) -> None:
    with TestClient(app) as client:
        assert client.post("/admin/dictionary/scan", json={"connector": "app1", "table": "ghost"}, headers=_h(client, "admin")).status_code == 404


def test_scan_unknown_connector_404(app) -> None:
    with TestClient(app) as client:
        assert client.post("/admin/dictionary/scan", json={"connector": "nope", "table": "customers"}, headers=_h(client, "admin")).status_code == 404
