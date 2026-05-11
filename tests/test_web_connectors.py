from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AuthSettings, ConnectorSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-conn-test-secret"


def _connectors_toml(db_url: str) -> str:
    return textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.db]
        type = "sql"
        pool = "default"

        [[connectors.db.queries]]
        name = "answer"
        sql = "SELECT 42 AS answer"

        [[connectors.db.queries]]
        name = "items"
        sql = "SELECT id, name, status FROM item WHERE (:status IS NULL OR status = :status) ORDER BY id"
        params = [{{ name = "status" }}]

        [[connectors.db.queries]]
        name = "add_item"
        sql = "INSERT INTO item (id, name, status) VALUES (:id, :name, 'on')"
        writable = true

        [[connectors.db.queries]]
        name = "bad"
        sql = "SELECT 1 FROM no_such_table"
        """
    )


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        engine = pools.engine("default")
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
            await conn.execute(text("INSERT INTO item (id, name, status) VALUES (1,'a','on'),(2,'b','off')"))
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.get_or_create_role("reader", permissions=["sql:db:answer", "sql:db:items"])
            await svc.get_or_create_role("dbuser", permissions=["sql:db:*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw", roles=["reader"])
            await svc.create_user("dbuser", password="dbuserpw", roles=["dbuser"])
            await svc.create_user("nobody", password="nobodypw")
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_connectors_toml(db_url))
    _seed(db_url)
    settings = Settings(
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings)


def _token(client: TestClient, username: str) -> str:
    return client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]


def _h(client: TestClient, username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, username)}"}


# --- discovery ------------------------------------------------------------- #


def test_list_connectors_filtered_by_permission(app) -> None:
    with TestClient(app) as client:
        admin = client.get("/api/connectors", headers=_h(client, "admin")).json()["connectors"]
        db = next(c for c in admin if c["name"] == "db")
        assert {q["name"] for q in db["queries"]} == {"answer", "items", "add_item", "bad"}
        assert all("sql" not in q for q in db["queries"])  # SQL text never leaves
        assert "pool" not in db

        reader = client.get("/api/connectors", headers=_h(client, "reader")).json()["connectors"]
        db = next(c for c in reader if c["name"] == "db")
        assert {q["name"] for q in db["queries"]} == {"answer", "items"}

        nobody = client.get("/api/connectors", headers=_h(client, "nobody")).json()["connectors"]
        assert nobody == []


def test_list_connectors_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/connectors").status_code == 401


def test_describe_one_connector(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/connectors/db", headers=_h(client, "reader"))
        assert r.status_code == 200 and r.json()["name"] == "db"
        assert client.get("/api/connectors/ghost", headers=_h(client, "admin")).status_code == 404
        assert client.get("/api/connectors/db", headers=_h(client, "nobody")).status_code == 404


# --- SQL: read ------------------------------------------------------------- #


def test_sql_get_select(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/sql/db/answer", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["connector"] == "db" and body["query"] == "answer"
        assert body["statement_type"] == "SELECT" and body["rows"] == [{"answer": 42}]
        assert [c["name"] for c in body["columns"]] == ["answer"]


def test_sql_get_with_query_string_params(app) -> None:
    with TestClient(app) as client:
        r = client.get("/api/sql/db/items?status=on", headers=_h(client, "admin"))
        assert [row["id"] for row in r.json()["rows"]] == [1]
        r = client.get("/api/sql/db/items", headers=_h(client, "admin"))
        assert [row["id"] for row in r.json()["rows"]] == [1, 2]


@pytest.mark.parametrize(("user", "code"), [("admin", 200), ("reader", 200), ("dbuser", 200), ("nobody", 403)])
def test_sql_permission(app, user, code) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/db/answer", headers=_h(client, user)).status_code == code


def test_sql_requires_auth(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/db/answer").status_code == 401


def test_sql_get_rejects_non_select(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/db/add_item", headers=_h(client, "admin")).status_code == 405


def test_sql_unknown_query_and_connector(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/db/ghost", headers=_h(client, "admin")).status_code == 404
        assert client.get("/api/sql/ghost/answer", headers=_h(client, "admin")).status_code == 404
        # ...but a caller without permission gets 403, not 404 — no enumeration.
        assert client.get("/api/sql/ghost/answer", headers=_h(client, "nobody")).status_code == 403


def test_sql_db_error_is_502(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/db/bad", headers=_h(client, "admin")).status_code == 502


# --- SQL: write ------------------------------------------------------------ #


def test_sql_post_write_requires_writable_and_permission(app) -> None:
    with TestClient(app) as client:
        # admin (superuser): runs the writable query
        r = client.post("/api/sql/db/add_item", json={"params": {"id": 99, "name": "z"}}, headers=_h(client, "admin"))
        assert r.status_code == 200 and r.json()["rowcount"] == 1 and r.json()["statement_type"] == "INSERT"
        # dbuser (sql:db:*) also covers it; the query's writable=true is the orthogonal gate
        r = client.post("/api/sql/db/add_item", json={"params": {"id": 88, "name": "w"}}, headers=_h(client, "dbuser"))
        assert r.status_code == 200
        # reader has only sql:db:answer + sql:db:items → 403 on add_item
        assert client.post("/api/sql/db/add_item", json={"params": {"id": 1, "name": "x"}}, headers=_h(client, "reader")).status_code == 403
        # the rows landed
        ids = [row["id"] for row in client.get("/api/sql/db/items", headers=_h(client, "admin")).json()["rows"]]
        assert 99 in ids and 88 in ids


def test_sql_post_param_forms(app) -> None:
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert [r["id"] for r in client.post("/api/sql/db/items", json={"params": {"status": "off"}}, headers=h).json()["rows"]] == [2]
        # flat object (no "params" key) is also accepted
        assert [r["id"] for r in client.post("/api/sql/db/items", json={"status": "on"}, headers=h).json()["rows"]] == [1]
        # no body → no params
        assert [r["id"] for r in client.post("/api/sql/db/items", headers=h).json()["rows"]] == [1, 2]
