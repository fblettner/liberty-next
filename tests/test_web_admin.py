from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, MenuSettings, Settings
from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.main import create_app

JWT_SECRET = "web-admin-test-secret"


def _toml(db_url: str, *, extra_connector: bool = False) -> str:
    base = textwrap.dedent(
        f"""
        [pools.default]
        url = "{db_url}"

        [connectors.db]
        type = "sql"
        pool = "default"

        [[connectors.db.queries]]
        name = "answer"
        sql = "SELECT 42 AS answer"
        """
    )
    if extra_connector:
        base += textwrap.dedent(
            """
            [connectors.db2]
            type = "sql"
            pool = "default"

            [[connectors.db2.queries]]
            name = "two"
            sql = "SELECT 2 AS two"
            """
        )
    return base


def _seed(db_url: str) -> None:
    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        db = AuthDatabase(pools, "default")
        await db.create_schema()
        async with db.session() as s:
            svc = AuthService(s)
            await svc.get_or_create_role("admin", permissions=["*"])
            await svc.create_user("admin", password="adminpw", is_superuser=True, roles=["admin"])
            await svc.create_user("reader", password="readerpw")  # not a superuser
        await pools.dispose()

    asyncio.run(go())


@pytest.fixture
def env(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(_toml(db_url))
    _seed(db_url)
    settings = Settings(
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        menus=MenuSettings(config_path=tmp_path / "menus.toml"),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
        ai=AISettings(enabled=False),
    )
    return create_app(settings), conn_toml, db_url


def _h(client: TestClient, username: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"username": username, "password": f"{username}pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_reload_requires_superuser(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        assert client.post("/admin/reload").status_code == 401
        assert client.post("/admin/reload", headers=_h(client, "reader")).status_code == 403


def test_reload_picks_up_new_connector(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        before = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert before == {"db"}

        conn_toml.write_text(_toml(db_url, extra_connector=True))
        r = client.post("/admin/reload", headers=h)
        assert r.status_code == 200 and r.json()["reloaded"] is True
        assert set(r.json()["connectors"]) == {"db", "db2"}

        # the new connector is live, and the old one still works
        after = {c["name"] for c in client.get("/api/connectors", headers=h).json()["connectors"]}
        assert after == {"db", "db2"}
        assert client.get("/api/sql/db2/two", headers=h).json()["rows"] == [{"two": 2}]
        assert client.get("/api/sql/db/answer", headers=h).json()["rows"] == [{"answer": 42}]

        # auth still works after the registry swap (re-pointed at the new pools)
        assert client.post("/auth/login", json={"username": "admin", "password": "adminpw"}).status_code == 200


def test_config_get_requires_superuser(env) -> None:
    app, conn_toml, _ = env
    with TestClient(app) as client:
        assert client.get("/admin/config/connectors").status_code == 401
        assert client.get("/admin/config/connectors", headers=_h(client, "reader")).status_code == 403
        r = client.get("/admin/config/connectors", headers=_h(client, "admin"))
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == str(conn_toml) and "[connectors.db]" in body["content"]


def test_config_put_validates_then_writes(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.put("/admin/config/connectors", json={"content": "x = ="}, headers=h).status_code == 422
        assert client.put(
            "/admin/config/connectors", json={"content": '[connectors.x]\ntype = "ftp"\n'}, headers=h
        ).status_code == 422
        # a non-superuser can't write
        assert client.put("/admin/config/connectors", json={"content": "# ok"}, headers=_h(client, "reader")).status_code == 403
        # valid content is written and reflected on the next GET, and Reload picks it up
        new_content = _toml(db_url, extra_connector=True)
        assert client.put("/admin/config/connectors", json={"content": new_content}, headers=h).json()["saved"] is True
        assert conn_toml.read_text() == new_content
        assert "[connectors.db2]" in client.get("/admin/config/connectors", headers=h).json()["content"]
        assert set(client.post("/admin/reload", headers=h).json()["connectors"]) == {"db", "db2"}


def test_config_schema_and_pools_get(env) -> None:
    app, conn_toml, _ = env
    with TestClient(app) as client:
        # schema (the builder UI's source of truth)
        assert client.get("/admin/config/schema").status_code == 401
        assert client.get("/admin/config/schema", headers=_h(client, "reader")).status_code == 403
        sch = client.get("/admin/config/schema", headers=_h(client, "admin")).json()
        assert "url" in sch["pool"]["properties"] and sch["pool"]["required"] == ["url"]
        # the structured pools view
        body = client.get("/admin/config/pools", headers=_h(client, "admin")).json()
        assert body["path"] == str(conn_toml)
        assert set(body["pools"]) == {"default"}
        assert body["pools"]["default"]["url"].startswith("sqlite+aiosqlite:///") and body["pools"]["default"]["pool_size"] == 5


def test_config_pools_put(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        assert client.put("/admin/config/pools", json={"pools": {}}, headers=_h(client, "reader")).status_code == 403
        # an invalid pool (no `url`) is rejected before anything is written
        assert client.put("/admin/config/pools", json={"pools": {"x": {"pool_size": 3}}}, headers=h).status_code == 422
        # valid: tweak `default` + add a `cache` pool; only the [pools.*] tables change, [connectors.db] survives
        new = {
            "default": {"url": db_url, "pool_size": 12, "schemas": {"PROD": "myprod"}},
            "cache": {"url": "sqlite+aiosqlite://"},
        }
        assert client.put("/admin/config/pools", json={"pools": new}, headers=h).json()["saved"] is True
        txt = conn_toml.read_text()
        assert "[connectors.db]" in txt and 'name = "answer"' in txt          # the connectors side is untouched
        assert "pool_size = 12" in txt and "[pools.cache]" in txt and "[pools.default.schemas]" in txt
        # GET reflects it, and Reload makes the new pool live
        after = client.get("/admin/config/pools", headers=h).json()["pools"]
        assert set(after) == {"default", "cache"} and after["default"]["pool_size"] == 12
        assert set(client.post("/admin/reload", headers=h).json()["pools"]) == {"default", "cache"}


def test_config_connectors_parsed_get_and_put(env) -> None:
    app, conn_toml, db_url = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the sql / api connector schemas (with their $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "queries" in sch["sql"]["properties"] and "QueryDef" in sch["sql"]["$defs"]
        assert "endpoints" in sch["api"]["properties"] and "EndpointDef" in sch["api"]["$defs"]
        # the structured connectors view
        body = client.get("/admin/config/connectors/parsed", headers=h).json()
        assert body["path"] == str(conn_toml) and set(body["connectors"]) == {"db"}
        db_conn = body["connectors"]["db"]
        assert db_conn["type"] == "sql" and any(q["name"] == "answer" for q in db_conn["queries"])
        # a non-superuser can't write; a malformed connector is rejected before anything lands
        assert client.put("/admin/config/connectors/parsed", json={"connectors": {}}, headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/connectors/parsed", json={"connectors": {"x": {"type": "ftp"}}}, headers=h).status_code == 422
        # valid: rename a query + add a connector; [pools.*] survives, the new connector is live after reload
        new = {
            "db": {"type": "sql", "pool": "default", "queries": [{"name": "the_answer", "sql": "SELECT 42 AS answer"}]},
            "extra": {"type": "sql", "pool": "default", "queries": [{"name": "two", "sql": "SELECT 2 AS two"}]},
        }
        assert client.put("/admin/config/connectors/parsed", json={"connectors": new}, headers=h).json()["saved"] is True
        txt = conn_toml.read_text()
        assert "[pools.default]" in txt and 'name = "the_answer"' in txt and "[connectors.extra]" in txt
        after = client.get("/admin/config/connectors/parsed", headers=h).json()["connectors"]
        assert set(after) == {"db", "extra"} and after["db"]["queries"][0]["name"] == "the_answer"
        r = client.post("/admin/reload", headers=h)
        assert set(r.json()["connectors"]) == {"db", "extra"}
        assert client.get("/api/sql/extra/two", headers=h).json()["rows"] == [{"two": 2}]
        assert client.get("/api/sql/db/the_answer", headers=h).json()["rows"] == [{"answer": 42}]


def test_config_dictionary_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    dict_toml = conn_toml.with_name("dictionary.toml")
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the DictionaryFile shape (with its $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "entries" in sch["dictionary"]["properties"]
        for nested in ("DictionaryEntry", "EnumDef", "LookupDef", "EnumValue", "DictionarySection"):
            assert nested in sch["dictionary"]["$defs"]
        # a fresh checkout has no dictionary.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/dictionary/parsed", headers=h).json()
        assert body["path"] == str(dict_toml) and body["dictionary"] == {}
        # permission gate + validation gate
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": {}}, headers=_h(client, "reader")).status_code == 403
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": {"entries": {"X": {"nope": 1}}}}, headers=h).status_code == 422
        # valid: shared entries + an enum + a per-connector overlay
        payload = {
            "default_language": "en",
            "entries": {
                "AUDIT_DATE": {"label": "Audit Date", "format": "date"},
                "USR_STATUS": {"label": "Status", "rules": "ENUM", "rules_values": "STATUS"},
            },
            "enums": {
                "STATUS": {"label": "Status", "values": [{"value": "A", "label": "Active"}, {"value": "I", "label": "Inactive"}]},
            },
            "connectors": {
                "db": {"entries": {"USR_NAME": {"label": "User Name", "l": {"fr": "Nom"}}}},
            },
        }
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": payload}, headers=h).json()["saved"] is True
        # the file was written + parses cleanly
        text = dict_toml.read_text()
        assert "[entries.AUDIT_DATE]" in text and "[enums.STATUS]" in text and "[connectors.db.entries.USR_NAME]" in text
        assert 'fr = "Nom"' in text
        # GET round-trips it (default-valued keys dropped — no `l = {}` on AUDIT_DATE)
        after = client.get("/admin/config/dictionary/parsed", headers=h).json()["dictionary"]
        assert after["entries"]["AUDIT_DATE"] == {"label": "Audit Date", "format": "date"}
        assert after["connectors"]["db"]["entries"]["USR_NAME"]["l"] == {"fr": "Nom"}
        # Reload picks it up — dictionary_entries goes from 0 to 2 (the shared entries)
        r = client.post("/admin/reload", headers=h).json()
        assert r["dictionary_entries"] == 2

        # framework-enum override: a user-defined [framework_enums.DATASOURCE_TYPE] replaces the
        # bundled values for that id, so /admin/config/schema merges the two on the fly.
        bundled_count = len(sch["framework_enums"]["DATASOURCE_TYPE"]["values"])
        assert bundled_count > 1  # the bundled DATASOURCE_TYPE ships several values
        payload2 = {
            **payload,
            "framework_enums": {
                "DATASOURCE_TYPE": {"label": "DB Engines", "values": [{"value": "duckdb", "label": "DuckDB"}]},
            },
        }
        assert client.put("/admin/config/dictionary/parsed", json={"dictionary": payload2}, headers=h).json()["saved"] is True
        assert "[framework_enums.DATASOURCE_TYPE]" in dict_toml.read_text()
        sch2 = client.get("/admin/config/schema", headers=h).json()
        # the override replaces the bundled list wholesale (label too)
        assert sch2["framework_enums"]["DATASOURCE_TYPE"]["label"] == "DB Engines"
        assert sch2["framework_enums"]["DATASOURCE_TYPE"]["values"] == [{"value": "duckdb", "label": "DuckDB"}]
        # other ids still come from the bundled set (untouched)
        assert sch2["framework_enums"]["HTTP_METHOD"]["values"] == sch["framework_enums"]["HTTP_METHOD"]["values"]


def test_config_menus_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    menus_toml = conn_toml.parent / "menus.toml"  # see env fixture — the same tmp_path
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the MenusFile shape (with its $defs)
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "menus" in sch["menus"]["properties"]
        for nested in ("AppMenu", "MenuItem"):
            assert nested in sch["menus"]["$defs"]
        # a fresh tmp dir has no menus.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/menus/parsed", headers=h).json()
        assert body["path"] == str(menus_toml) and body["menus"] == {}
        # permission gate + validation gate (an unknown parent / type mismatch is rejected)
        assert client.put("/admin/config/menus/parsed", json={"menus": {}}, headers=_h(client, "reader")).status_code == 403
        bad = {"app1": {"items": [{"id": "a", "label": "A", "parent": "nope"}]}}
        assert client.put("/admin/config/menus/parsed", json={"menus": bad}, headers=h).status_code == 422
        # valid: a folder + two leaves underneath
        payload = {
            "nomasx1": {
                "label": "NOMASX-1",
                "items": [
                    {"id": "security", "label": "Security", "l": {"fr": "Sécurité"}, "icon": "shield"},
                    {"id": "users", "parent": "security", "label": "Users", "type": "query", "target": "security_users_get"},
                    {"id": "roles", "parent": "security", "label": "Roles", "type": "query", "target": "security_roles_get"},
                ],
            },
        }
        r = client.put("/admin/config/menus/parsed", json={"menus": payload}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True
        text = menus_toml.read_text()
        assert "[[menus.nomasx1.items]]" in text and 'id = "security"' in text and 'fr = "Sécurité"' in text
        after = client.get("/admin/config/menus/parsed", headers=h).json()["menus"]
        assert set(after) == {"nomasx1"}
        assert after["nomasx1"]["label"] == "NOMASX-1"
        assert [it["id"] for it in after["nomasx1"]["items"]] == ["security", "users", "roles"]
        # Reload picks it up — the app's menu count goes from 0 to 1
        assert client.post("/admin/reload", headers=h).json()["menu_apps"] == ["nomasx1"]


def test_oidc_callback_fragment_redirect() -> None:
    from liberty.config import OIDCSettings

    s = OIDCSettings(enabled=True, discovery_url="https://idp.test/.well-known", frontend_redirect="https://app.test/oidc/callback")
    assert s.frontend_redirect == "https://app.test/oidc/callback"
    # (the full OIDC flow needs a real IdP; this just pins the new setting + that the
    # callback route now returns a redirect when frontend_redirect is set — see liberty/auth/routes.py)
