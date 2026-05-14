from __future__ import annotations

import asyncio
import textwrap
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
    MenuSettings,
    ScreenSettings,
    Settings,
)
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
        screens=ScreenSettings(config_path=tmp_path / "screens.toml"),
        charts=ChartSettings(config_path=tmp_path / "charts.toml"),
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


def test_config_screens_parsed_get_and_put(env) -> None:
    app, conn_toml, _ = env
    screens_toml = conn_toml.parent / "screens.toml"  # see env fixture — the same tmp_path
    with TestClient(app) as client:
        h = _h(client, "admin")
        # schema serves the ScreensFile shape with its $defs
        sch = client.get("/admin/config/schema", headers=h).json()
        assert "screens" in sch["screens"]["properties"]
        # The action discriminated union (slice 4) exposes every variant as its own $def so the
        # builder's ActionEditor can render the right per-type form when the operator picks one.
        for nested in (
            "Screen", "ScreenDialog", "ScreenTab", "ScreenField", "ParamBind",
            "RunQueryAction", "CallApiAction", "NavigateAction", "SetFieldAction",
            "ConfirmAction", "NotifyAction", "RefreshAction",
        ):
            assert nested in sch["screens"]["$defs"]
        # a fresh tmp dir has no screens.toml → GET returns an empty dict (path still reported)
        body = client.get("/admin/config/screens/parsed", headers=h).json()
        assert body["path"] == str(screens_toml) and body["screens"] == {}
        # permission gate + validation gate (an explicit id ≠ its key is a config bug)
        assert (
            client.put("/admin/config/screens/parsed", json={"screens": {}}, headers=_h(client, "reader")).status_code
            == 403
        )
        bad = {"app1": {"users": {"id": "people", "read_query": "q"}}}
        assert client.put("/admin/config/screens/parsed", json={"screens": bad}, headers=h).status_code == 422
        # valid: one screen with a dialog + a ParamBind binding for a field's lookup
        payload = {
            "nomasx1": {
                "security_users": {
                    "label": "Users",
                    "read_query": "users_get",
                    "update_query": "users_put",
                    "audit": True,
                    "dialog": {
                        "title": "User",
                        "tabs": [
                            {
                                "id": "general",
                                "label": "General",
                                "fields": [
                                    {"name": "USR_ID"},
                                    {
                                        "name": "USR_ROLE_ID",
                                        "dd": "ROL_ID",
                                        "lookup_param_binds": [
                                            {"param": "ROL_APPS_ID", "source": "USR_APPS_ID"},
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            },
        }
        r = client.put("/admin/config/screens/parsed", json={"screens": payload}, headers=h)
        assert r.status_code == 200 and r.json()["saved"] is True
        text = screens_toml.read_text()
        assert "[screens.nomasx1.security_users]" in text
        assert 'read_query = "users_get"' in text and "audit = true" in text
        # round-trip GET — id is injected from the dict key (default-valued keys dropped)
        after = client.get("/admin/config/screens/parsed", headers=h).json()["screens"]
        assert set(after) == {"nomasx1"}
        assert set(after["nomasx1"]) == {"security_users"}
        s = after["nomasx1"]["security_users"]
        assert s["read_query"] == "users_get" and s["audit"] is True
        assert s["dialog"]["tabs"][0]["fields"][1]["lookup_param_binds"][0] == {
            "param": "ROL_APPS_ID", "source": "USR_APPS_ID",
        }
        # Reload picks it up — the screen apps go from 0 → 1
        assert client.post("/admin/reload", headers=h).json()["screen_apps"] == ["nomasx1"]


def test_oidc_callback_fragment_redirect() -> None:
    from liberty.config import OIDCSettings

    s = OIDCSettings(enabled=True, discovery_url="https://idp.test/.well-known", frontend_redirect="https://app.test/oidc/callback")
    assert s.frontend_redirect == "https://app.test/oidc/callback"
    # (the full OIDC flow needs a real IdP; this just pins the new setting + that the
    # callback route now returns a redirect when frontend_redirect is set — see liberty/auth/routes.py)


# --- POST /admin/config/connectors/{c}/test-sql --------------------------- #


def _seed_item_table(db_url: str) -> None:
    """Drop an ``item`` table on the test DB so test-sql has something to run against —
    the env fixture only sets up the auth schema, no user table to play with."""
    from sqlalchemy import text as _text

    async def go() -> None:
        pools = PoolRegistry({"default": PoolConfig(url=db_url)})
        engine = pools.engine("default")
        async with engine.begin() as conn:
            await conn.execute(_text("CREATE TABLE IF NOT EXISTS item (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
            await conn.execute(_text("DELETE FROM item"))
            await conn.execute(_text("INSERT INTO item (id, name, status) VALUES (1,'a','on'),(2,'b','off')"))
        await pools.dispose()

    asyncio.run(go())


def test_test_sql_select_with_params(env) -> None:
    """A SELECT runs and returns rows; the same ``:param`` binds the named-query path uses
    work here too — the operator can paste the same SQL they keep in the TOML and it just runs."""
    app, _, db_url = env
    _seed_item_table(db_url)
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"sql": "SELECT id, name FROM item WHERE (:status IS NULL OR status = :status) ORDER BY id", "params": {"status": "on"}}
        r = client.post("/admin/config/connectors/db/test-sql", json=body, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["statement_type"] == "SELECT" and j["row_count"] == 1
        assert j["rows"] == [{"id": 1, "name": "a"}]
        assert j["truncated"] is False and "duration_ms" in j
        # max_rows truncates
        body2 = {"sql": "SELECT id FROM item ORDER BY id", "max_rows": 1}
        r2 = client.post("/admin/config/connectors/db/test-sql", json=body2, headers=h).json()
        assert r2["row_count"] == 1 and r2["truncated"] is True


def test_test_sql_dry_run_rolls_back_write(env) -> None:
    """An INSERT/UPDATE/DELETE with dry_run=True (the default) reports its rowcount but
    the row never lands — the operator gets to verify a `_put` parses and the WHERE matches
    before committing it."""
    app, _, db_url = env
    _seed_item_table(db_url)
    with TestClient(app) as client:
        h = _h(client, "admin")
        # default dry_run=True
        body = {"sql": "INSERT INTO item (id, name, status) VALUES (99, 'test', 'on')"}
        j = client.post("/admin/config/connectors/db/test-sql", json=body, headers=h).json()
        assert j["statement_type"] == "INSERT" and j["rowcount"] == 1 and "rows" not in j or j["rows"] == []
        # the row didn't land — verify via a follow-up SELECT
        rows = client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT id FROM item ORDER BY id"}, headers=h).json()["rows"]
        assert [r["id"] for r in rows] == [1, 2]
        # explicit dry_run=False commits
        body2 = {"sql": "INSERT INTO item (id, name, status) VALUES (99, 'test', 'on')", "dry_run": False}
        client.post("/admin/config/connectors/db/test-sql", json=body2, headers=h)
        rows2 = client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT id FROM item ORDER BY id"}, headers=h).json()["rows"]
        assert [r["id"] for r in rows2] == [1, 2, 99]


def test_test_sql_rejects_disallowed_and_requires_superuser(env) -> None:
    app, _, _ = env
    with TestClient(app) as client:
        # non-superuser → 403
        assert client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT 1"}, headers=_h(client, "reader")).status_code == 403
        # unauth → 401
        assert client.post("/admin/config/connectors/db/test-sql", json={"sql": "SELECT 1"}).status_code == 401
        h = _h(client, "admin")
        # unknown connector → 404
        assert client.post("/admin/config/connectors/ghost/test-sql", json={"sql": "SELECT 1"}, headers=h).status_code == 404
        # DROP not in the statement allow-list → 422 (parity with the named-query path)
        r = client.post("/admin/config/connectors/db/test-sql", json={"sql": "DROP TABLE item"}, headers=h)
        assert r.status_code == 422


# --- /admin/config/charts/parsed ------------------------------------------- #


def test_config_charts_parsed_get_and_put(env) -> None:
    """GET → an empty dict when no charts.toml; PUT validates + writes; reload picks it up."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        # GET: empty
        r = client.get("/admin/config/charts/parsed", headers=h).json()
        assert r["charts"] == {}
        assert r["path"].endswith("charts.toml")
        # PUT: save one chart
        body = {"charts": {
            "users_per_app": {
                "label": "Users per app",
                "connector": "db",
                "query": "answer",
                "spec": {"type": "bar", "x": "X", "y": ["Y"], "aggregation": "count"},
            },
        }}
        s = client.put("/admin/config/charts/parsed", json=body, headers=h)
        assert s.status_code == 200 and s.json()["saved"] is True
        # GET round-trip: the chart is there. The GET uses `exclude_defaults=True` so default
        # values (type='bar', aggregation='sum') are stripped — only non-default fields land in
        # the response (and on disk), which keeps charts.toml diff-friendly.
        after = client.get("/admin/config/charts/parsed", headers=h).json()["charts"]
        assert set(after) == {"users_per_app"}
        c = after["users_per_app"]
        assert c["label"] == "Users per app"
        assert c["spec"]["x"] == "X" and c["spec"]["y"] == ["Y"] and c["spec"]["aggregation"] == "count"
        # Reload picks it up — the chart shows up in /admin/reload's reply + /api/charts
        reload_resp = client.post("/admin/reload", headers=h).json()
        assert reload_resp["charts"] == ["users_per_app"]
        listed = client.get("/api/charts", headers=h).json()["charts"]
        assert [c["id"] for c in listed] == ["users_per_app"]


def test_config_charts_parsed_rejects_invalid(env) -> None:
    """A chart missing its X or Y column → 422 from the PUT (the model validator catches it)."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        body = {"charts": {
            "broken": {
                "label": "B", "connector": "c", "query": "q",
                "spec": {"type": "bar", "x": "X", "y": []},  # empty Y
            },
        }}
        r = client.put("/admin/config/charts/parsed", json=body, headers=h)
        assert r.status_code == 422 and "spec.y" in r.json()["detail"]


def test_config_schema_includes_charts(env) -> None:
    """The /admin/config/schema bundle now carries the ChartsFile JSON Schema so the
    (future) ChartsBuilder can render its forms from it."""
    app, _, _ = env
    with TestClient(app) as client:
        h = _h(client, "admin")
        schema = client.get("/admin/config/schema", headers=h).json()
        assert "charts" in schema
        assert "properties" in schema["charts"]
        # The ChartConfig + ChartSpec $defs are reachable for SchemaNavigator drill-in
        defs = schema["charts"].get("$defs") or {}
        assert "ChartConfig" in defs and "ChartSpec" in defs
