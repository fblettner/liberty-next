from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthService
from liberty.config import AISettings, AppSettings, AuthSettings, ConnectorSettings, Settings
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
        sql = "INSERT INTO item (id, name, status) VALUES (:ID, :NAME, 'on')"
        writable = true

        [[connectors.db.queries]]
        name = "upd_item"
        sql = "UPDATE item SET name = :NAME WHERE id = :ID_ORIGINAL"
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
        app=AppSettings(static_dir=""),
        connectors=ConnectorSettings(config_path=Path(conn_toml)),
        auth=AuthSettings(backend="db", jwt_secret=JWT_SECRET, pool="default"),
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
        assert {q["name"] for q in db["queries"]} == {"answer", "items", "add_item", "upd_item", "bad"}
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


def test_sql_get_summary_and_detail(app) -> None:
    with TestClient(app) as client:
        # Summary: GROUP BY status + COUNT(*) → one row per status with a _count.
        r = client.get("/api/sql/db/items?_summary=1&_group=status", headers=_h(client, "admin"))
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert {row["status"]: row["_count"] for row in rows} == {"on": 1, "off": 1}
        # Detail: the group's dimension value rides as a normal param; returns just that group.
        r = client.get("/api/sql/db/items?_group=status&status=on", headers=_h(client, "admin"))
        assert [row["id"] for row in r.json()["rows"]] == [1]


def test_parse_group_spec() -> None:
    from liberty.web.connectors import _parse_group_spec
    assert _parse_group_spec("A, B~day ,C~bogus") == [("A", None), ("B", "day"), ("C", None)]
    assert _parse_group_spec("") == []


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
        # admin (superuser): runs the writable query (binds are UPPERCASE, JDE convention)
        r = client.post("/api/sql/db/add_item", json={"params": {"ID": 99, "NAME": "z"}}, headers=_h(client, "admin"))
        assert r.status_code == 200 and r.json()["rowcount"] == 1 and r.json()["statement_type"] == "INSERT"
        # dbuser (sql:db:*) also covers it; the query's writable=true is the orthogonal gate
        r = client.post("/api/sql/db/add_item", json={"params": {"ID": 88, "NAME": "w"}}, headers=_h(client, "dbuser"))
        assert r.status_code == 200
        # reader has only sql:db:answer + sql:db:items → 403 on add_item
        assert client.post("/api/sql/db/add_item", json={"params": {"ID": 1, "NAME": "x"}}, headers=_h(client, "reader")).status_code == 403
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


# --- SQL: pool schema introspection (Phase 7 — SQL editor / wizard) -------- #


def test_pool_schema_lists_tables_and_columns(app) -> None:
    """``GET /api/sql/{c}/_schema`` returns the connector's pool's tables/views + columns —
    powers the SQL editor's autocomplete and the wizard's table picker. Superuser only; the
    `item` table seeded by _seed() must appear with its three columns."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/_schema", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pool"] == "default" and body["dialect"] == "sqlite" and body["truncated"] is False
        by_name = {t["name"]: t for t in body["tables"]}
        item = by_name["item"]
        assert item["kind"] == "table"
        assert [c["name"] for c in item["columns"]] == ["id", "name", "status"]
        # types come through best-effort (the in-memory dialect labels INTEGER/TEXT)
        assert all("type" in c for c in item["columns"])


def test_pool_schema_requires_superuser(app) -> None:
    """The introspection leaks every table on the pool — non-superusers should not see it,
    even if they hold ``sql:db:*`` (which only authorizes named queries). 403 either way."""
    with TestClient(app) as client:
        # dbuser holds `sql:db:*` (every named query) — still no — schema endpoint is admin-only
        assert client.get("/api/sql/db/_schema", headers=_h(client, "dbuser")).status_code == 403
        assert client.get("/api/sql/db/_schema", headers=_h(client, "nobody")).status_code == 403
        # unauth → 401
        assert client.get("/api/sql/db/_schema").status_code == 401


def test_pool_schema_404_when_connector_missing(app) -> None:
    with TestClient(app) as client:
        assert client.get("/api/sql/ghost/_schema", headers=_h(client, "admin")).status_code == 404


def test_pool_schemas_lists_schema_names_only(app) -> None:
    """``GET /api/sql/{c}/_schemas`` — the lightweight schema list. On SQLite the test fixture
    doesn't have multiple schemas, so the response carries an empty list; the dialect + pool
    fields are populated either way. The CRUD wizard's schema picker uses this endpoint so
    Oracle pools with many schemas don't pay the full-catalog walk before the operator's first
    interaction. Same superuser gate as ``/_schema``."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/_schemas", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pool"] == "default" and body["dialect"] == "sqlite"
        assert isinstance(body["schemas"], list)
        # Permission gate: same as /_schema.
        assert client.get("/api/sql/db/_schemas", headers=_h(client, "dbuser")).status_code == 403
        assert client.get("/api/sql/db/_schemas").status_code == 401
        # Unknown connector → 404.
        assert client.get("/api/sql/ghost/_schemas", headers=_h(client, "admin")).status_code == 404


def test_pool_schema_with_schema_query_param_scopes_walk(app) -> None:
    """``?schema=<sch>`` filter restricts the table walk to one schema. The seeded SQLite fixture
    only has a default schema (None) so passing a nonexistent schema returns zero tables —
    proves the filter is applied rather than ignored. Real-world Oracle case is covered by the
    introspector's unit-level behaviour (``_walk_sync(only_schema=…)``)."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/_schema?schema=nosuch", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        assert r.json()["tables"] == []


def test_pool_schema_name_like_filter_narrows_table_list(app) -> None:
    """``?name_like=<pat>`` filters table / view names with SQL-LIKE wildcards (``%``).
    Names that don't match are dropped *before* the per-table column fetch — the wizard
    uses this on Oracle so the operator doesn't pay for every column on every table when
    they only need ``F009%``. Matching is case-insensitive."""
    with TestClient(app) as client:
        # The fixture seeds an ``item`` table (see tests/test_web_connectors.py::_seed).
        # ``ite%`` should match; ``XYZ%`` shouldn't.
        r = client.get("/api/sql/db/_schema?name_like=ite%25", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        names = [t["name"] for t in r.json()["tables"]]
        assert names == ["item"]
        r = client.get("/api/sql/db/_schema?name_like=XYZ%25", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        assert r.json()["tables"] == []
        # Case-insensitive — uppercase pattern still matches lowercase ``item``.
        r = client.get("/api/sql/db/_schema?name_like=ITEM", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        assert [t["name"] for t in r.json()["tables"]] == ["item"]


# ── streaming (``?_stream=1``) ─────────────────────────────────────────────────────────────


def _parse_ndjson(body: bytes) -> list[dict]:
    """Decode an NDJSON response body into a list of event dicts. Empty lines (which
    StreamingResponse may emit at the seam between chunks) are filtered out so the test
    asserts on actual events only."""
    import json
    out = []
    for line in body.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def test_sql_stream_emits_meta_rows_done(app) -> None:
    """``?_stream=1`` returns NDJSON: one ``meta`` event with the resolved columns, one
    or more ``rows`` events, exactly one ``done`` event at the end. ``sent`` on each
    ``rows`` event is the cumulative count after that chunk."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/items?_stream=1&_chunk_size=1", headers=_h(client, "admin"))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = _parse_ndjson(r.content)
        # 1 meta + 2 rows (chunk_size=1, 2 rows in the fixture) + 1 done = 4
        assert [e["kind"] for e in events] == ["meta", "rows", "rows", "done"]
        meta = events[0]
        assert [c["name"] for c in meta["columns"]] == ["id", "name", "status"]
        assert meta["chunk_size"] == 1
        # Cumulative `sent` is 1, then 2.
        assert [e["sent"] for e in events[1:3]] == [1, 2]
        # First row payload.
        assert events[1]["rows"][0]["name"] == "a"
        assert events[2]["rows"][0]["name"] == "b"
        done = events[-1]
        assert done["total"] == 2
        assert done["truncated"] is False
        assert done["rowcount"] == -1


def test_sql_stream_post_path_and_query_params(app) -> None:
    """POST + ``?_stream=1`` also streams. ``status`` is bound from the request body and
    narrows the result set — the fixture's 2-row table has one row per status."""
    with TestClient(app) as client:
        r = client.post(
            "/api/sql/db/items?_stream=1",
            headers=_h(client, "admin"),
            json={"params": {"status": "on"}},
        )
        assert r.status_code == 200, r.text
        events = _parse_ndjson(r.content)
        assert [e["kind"] for e in events][0] == "meta"
        assert events[-1]["kind"] == "done"
        rows = [r for e in events if e["kind"] == "rows" for r in e["rows"]]
        assert len(rows) == 1 and rows[0]["status"] == "on"


def test_sql_stream_permission_gate_short_circuits(app) -> None:
    """Permission is checked **before** the connector is opened, so an unauthorised user
    gets a clean 403 — never a half-streamed NDJSON body."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/items?_stream=1", headers=_h(client, "nobody"))
        assert r.status_code == 403


def test_sql_stream_rejects_non_select(app) -> None:
    """Streaming is SELECT-only. A non-SELECT query gets a clean 405 up front — the
    NDJSON body never starts, so the consumer's status check catches it normally."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/add_item?_stream=1", headers=_h(client, "admin"))
        assert r.status_code == 405


def test_sql_stream_unknown_query_is_404(app) -> None:
    """Pre-flight resolution of the query catches a typo as a 404 before any byte ships.
    Permission has to pass first — admin has ``*``, so the 403 path doesn't kick in."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/no_such?_stream=1", headers=_h(client, "admin"))
        assert r.status_code == 404


def test_sql_stream_db_error_becomes_inline_error_event(app) -> None:
    """A SQL error raised *mid-stream* (after headers have shipped) can't change the
    HTTP status — instead, the NDJSON sequence terminates with an ``error`` event so the
    consumer's parser sees a clean end. ``bad`` references a non-existent table."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/bad?_stream=1", headers=_h(client, "admin"))
        # Status is 200 (headers shipped before the first execute() call returns).
        assert r.status_code == 200
        events = _parse_ndjson(r.content)
        # The first event is `meta` only when the cursor actually opened — for a bad
        # query the error fires before any partition lands. Either shape is acceptable;
        # what matters is that the last event has `kind == "error"`.
        assert events[-1]["kind"] == "error"
        assert "no_such_table" in events[-1]["detail"] or "no such table" in events[-1]["detail"]


def test_sql_stream_chunk_size_clamped(app) -> None:
    """``_chunk_size`` is clamped to ``[1, MAX_CHUNK_SIZE]``. A bogus value (0 / negative)
    falls back to the default; a huge one tops out at MAX_CHUNK_SIZE."""
    with TestClient(app) as client:
        # 0 → default (100). Two-row fixture fits in one chunk.
        r = client.get("/api/sql/db/items?_stream=1&_chunk_size=0", headers=_h(client, "admin"))
        events = _parse_ndjson(r.content)
        assert events[0]["chunk_size"] == 100
        # Huge → 5000 (MAX_CHUNK_SIZE).
        r = client.get("/api/sql/db/items?_stream=1&_chunk_size=999999", headers=_h(client, "admin"))
        events = _parse_ndjson(r.content)
        assert events[0]["chunk_size"] == 5000


def test_sql_stream_respects_limit(app) -> None:
    """``?_limit=N`` applies the same row cap as the non-streaming mode — the cursor
    stops after N rows and ``done.truncated = True`` signals it."""
    with TestClient(app) as client:
        r = client.get("/api/sql/db/items?_stream=1&_limit=1&_chunk_size=1", headers=_h(client, "admin"))
        events = _parse_ndjson(r.content)
        assert [e["kind"] for e in events] == ["meta", "rows", "done"]
        assert events[-1]["total"] == 1
        assert events[-1]["truncated"] is True


def test_public_connector_keeps_empty_switcher_app_hides_permission_filtered() -> None:
    """A connector with NO queries at all but ``show_in_switcher`` is an app whose screens read
    from other connectors (e.g. nomajde after its tables/lookups moved to jdedwards) — keep it so
    the app switcher (which filters /api/connectors by menu app name) can still find it. A
    connector that HAS queries but none the caller may use stays hidden (permission filter, no
    name leak)."""
    from liberty.web.deps import public_connector

    class _P:
        def __init__(self, allow: bool) -> None:
            self._allow = allow

        def has_permission(self, _perm: str) -> bool:
            return self._allow

    empty = {"name": "nomajde", "type": "sql", "tables": [], "queries": [],
             "sequences": [], "lookups": [], "show_in_switcher": True}
    out = public_connector(empty, _P(True))
    assert out is not None and out["name"] == "nomajde" and out["queries"] == []

    # opted OUT of the switcher → hidden when empty
    assert public_connector({**empty, "show_in_switcher": False}, _P(True)) is None

    # has queries but all forbidden → hidden (permission filter), even with show_in_switcher
    has_q = {"name": "secret", "type": "sql", "tables": [],
             "queries": [{"name": "q1", "sql": "SELECT 1"}], "sequences": [], "lookups": [],
             "show_in_switcher": True}
    assert public_connector(has_q, _P(False)) is None
    assert public_connector(has_q, _P(True)) is not None


def test_find_screen_resolves_column_group_write_query() -> None:
    """A column-group write query resolves to its screen so the group write inherits the screen's
    column hints (a group column's ``dd`` rule fires) — marked ``group`` so the route does NOT also
    apply the screen's main audit table / change-capture to the related-table write."""
    from liberty.web.connectors import _find_screen_for_query, _column_hints_for
    from liberty.screens.config import parse_screens

    sf = parse_screens({"screens": {"nomajde": {"f0092": {
        "connector": "jdedwards",
        "read_query": "f0092_get", "update_query": "f0092_put", "insert_query": "f0092_post",
        "column_groups": [{"id": "f00921", "connector": "jdedwards",
                           "update_query": "f00921_put", "insert_query": "f00921_post",
                           "delete_query": "f00921_delete", "key_columns": ["ULUSER"]}],
        "columns": [{"name": "ULMUSE", "dd": "MUSE", "group": "f00921"}],
    }}}})
    s, slot, app = _find_screen_for_query(sf, "jdedwards", "f0092_put")
    assert s is not None and slot == "update" and app == "nomajde"
    s, slot, app = _find_screen_for_query(sf, "jdedwards", "f00921_post")
    assert s is not None and slot == "group" and app == "nomajde"
    assert "ULMUSE" in {c.name for c in (_column_hints_for(s) or [])}   # group column's dd is available


def test_resolve_action_screen_for_change_capture() -> None:
    """A tagged screen-action call (``_change_context`` {app, screen}) resolves to the originating
    screen so its run_query write / call_api / call_plugin lands in THAT screen's package."""
    from liberty.web.connectors import _resolve_action_screen
    from liberty.screens.config import parse_screens
    sf = parse_screens({"screens": {"nomajde": {"f0092": {
        "read_query": "f0092_get", "change_tracked": True, "change_entity": "user",
    }}}})
    s = _resolve_action_screen(sf, {"app": "nomajde", "screen": "f0092"})
    assert s is not None and s.change_tracked is True and s.change_entity == "user"
    # Missing context / unknown screen / no screens → None (no capture).
    assert _resolve_action_screen(sf, None) is None
    assert _resolve_action_screen(sf, {"app": "nomajde", "screen": "nope"}) is None
    assert _resolve_action_screen(sf, {"app": "x", "screen": "f0092"}) is None
    assert _resolve_action_screen(None, {"app": "nomajde", "screen": "f0092"}) is None


def test_import_validate_is_a_dry_run(app) -> None:
    """``commit=false`` runs each row against the DB but rolls it back: a valid row + a PK-clash row
    are reported, and the table is left untouched."""
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "admin"), json={
            "mode": "insert", "insert_query": "add_item",
            "rows": [{"id": 5, "name": "x"}, {"id": 1, "name": "dup"}], "commit": False,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["committed"] is False and body["valid"] == 1 and body["invalid"] == 1
        assert body["results"][0]["ok"] is True and body["results"][0]["action"] == "insert"
        assert body["results"][1]["ok"] is False and body["results"][1]["error"]
        assert "[SQL:" not in body["results"][1]["error"]  # the verbose SQL/params tail is trimmed off
        # Nothing persisted — id=5 was rolled back.
        items = client.get("/api/sql/db/items", headers=_h(client, "admin")).json()["rows"]
        assert {it["id"] for it in items} == {1, 2}


def test_import_insert_flags_existing_rows_via_pk(app) -> None:
    """Insert-only: a row whose key already exists is flagged invalid because the dry-run INSERT hits
    the table's PRIMARY KEY (the real constraint), exactly as a real save would — no separate probe."""
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "admin"), json={
            "mode": "insert", "insert_query": "add_item",
            "rows": [{"id": 1, "name": "dup"}, {"id": 9, "name": "new"}], "commit": False,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["results"][0]["ok"] is False and body["results"][0]["error"]  # PK / UNIQUE violation
        assert body["results"][1]["ok"] is True
        assert body["valid"] == 1 and body["invalid"] == 1


def test_import_commit_persists_valid_rows(app) -> None:
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "admin"), json={
            "mode": "insert", "insert_query": "add_item",
            "rows": [{"id": 5, "name": "x"}], "commit": True,
        })
        assert r.status_code == 200, r.text
        assert r.json()["valid"] == 1
        items = client.get("/api/sql/db/items", headers=_h(client, "admin")).json()["rows"]
        assert {it["id"] for it in items} == {1, 2, 5}


def test_import_upsert_routes_update_vs_insert(app) -> None:
    """upsert decides per row via a dry-run UPDATE probe: existing key → UPDATE, new key → INSERT."""
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "admin"), json={
            "mode": "upsert", "insert_query": "add_item", "update_query": "upd_item",
            "rows": [{"id": 1, "name": "A1"}, {"id": 9, "name": "N9"}], "commit": True,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert [x["action"] for x in body["results"]] == ["update", "insert"]
        assert body["valid"] == 2
        items = {it["id"]: it["name"] for it in client.get("/api/sql/db/items", headers=_h(client, "admin")).json()["rows"]}
        assert items[1] == "A1" and items[9] == "N9"


def test_import_requires_query_permission(app) -> None:
    """The caller must hold ``sql:<conn>:<query>`` on the insert/update query — reader lacks add_item."""
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "reader"), json={
            "mode": "insert", "insert_query": "add_item", "rows": [{"id": 7, "name": "z"}], "commit": False,
        })
        assert r.status_code == 403


def test_import_rejects_bad_mode(app) -> None:
    with TestClient(app) as client:
        r = client.post("/api/sql/db/_import", headers=_h(client, "admin"), json={
            "mode": "sideways", "insert_query": "add_item", "rows": [], "commit": False,
        })
        assert r.status_code == 400
