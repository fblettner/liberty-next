from __future__ import annotations

import tomllib

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import ApiConnectorConfig, SqlConnectorConfig, parse_connectors
from liberty.migrate_cli import main as migrate_main
from liberty.migrations import (
    make_engine,
    merge_connectors,
    migrate_api,
    migrate_sql_queries,
    read_api,
    read_sql_queries,
    render_toml,
    slugify,
)


# --------------------------------------------------------------------------- #
# pure transformation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Get Users", "get_users"),
        ("  Mixed-CASE / weird!!  ", "mixed_case_weird"),
        ("123abc", "x123abc"),
        ("", "x"),
        (None, "x"),
    ],
)
def test_slugify(raw, expected) -> None:
    assert slugify(raw) == expected


_QUERIES = [
    {"query_id": 1, "query_label": "Users List", "query_type": "TABLE"},
    {"query_id": 2, "query_label": "Delete User", "query_type": "FORM"},
    {"query_id": 3, "query_label": "", "query_type": "TABLE"},
    {"query_id": 4, "query_label": "Twins", "query_type": "TABLE"},
]
_SQL_ROWS = [
    # query 1: two distinct dbtype variants → a {default, oracle} dialect map
    {"query_id": 1, "query_dbtype": "generic", "query_crud": "SELECT", "query_pool": "default",
     "query_sqlquery": "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status", "query_orderby": "usr_name"},
    {"query_id": 1, "query_dbtype": "oracle", "query_crud": "SELECT", "query_pool": "default",
     "query_sqlquery": "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status FETCH FIRST 50 ROWS ONLY", "query_orderby": "usr_name"},
    {"query_id": 2, "query_dbtype": "postgres", "query_crud": "DELETE", "query_pool": "default",
     "query_sqlquery": "DELETE FROM ly_users WHERE usr_id = :id", "query_orderby": None},
    {"query_id": 3, "query_dbtype": "postgres", "query_crud": "SELECT", "query_pool": "nomasx1",
     "query_sqlquery": "SELECT * FROM f0101", "query_orderby": None},
    # query 4: two dbtypes but identical SQL → collapses to a plain string
    {"query_id": 4, "query_dbtype": "postgres", "query_crud": "SELECT", "query_pool": "default", "query_sqlquery": "SELECT 1 AS x", "query_orderby": None},
    {"query_id": 4, "query_dbtype": "oracle", "query_crud": "SELECT", "query_pool": "default", "query_sqlquery": "SELECT 1 AS x", "query_orderby": None},
    {"query_id": 99, "query_dbtype": "postgres", "query_crud": "SELECT", "query_pool": "default", "query_sqlquery": "  ", "query_orderby": None},  # blank → skipped
]


def test_migrate_sql_queries() -> None:
    out = migrate_sql_queries(_QUERIES, _SQL_ROWS)
    assert set(out["pools"]) == {"default", "nomasx1"}
    assert out["pools"]["default"]["url"] == "${LIBERTY_DB_URL_DEFAULT}"
    conns = out["connectors"]
    assert set(conns) == {"default", "nomasx1"}
    assert conns["default"]["type"] == "sql" and conns["default"]["pool"] == "default"

    by_name = {q["name"]: q for q in conns["default"]["queries"]}
    # query 1: one v2 query (no _<dbtype> suffix); SQL is a {default, oracle} map
    ul = by_name["users_list_select"]
    assert ul["label"] == "Users List"
    assert "writable" not in ul  # SELECT → omitted (defaults to false)
    assert isinstance(ul["sql"], dict)
    assert set(ul["sql"]) == {"default", "oracle"}
    assert ul["sql"]["default"].endswith("ORDER BY usr_name")          # generic variant → default
    assert "FETCH FIRST 50 ROWS ONLY" in ul["sql"]["oracle"]            # oracle variant kept distinct
    # query 2 (DELETE) → single dbtype → plain-string sql, writable=true, no ORDER BY
    assert by_name["delete_user_delete"]["writable"] is True
    assert by_name["delete_user_delete"]["sql"] == "DELETE FROM ly_users WHERE usr_id = :id"
    # query 4: two dbtypes but identical SQL → collapsed to a plain string
    assert by_name["twins_select"]["sql"] == "SELECT 1 AS x"
    # blank-SQL row 99 skipped
    assert all("99" not in n for n in by_name)
    # unlabelled query 3 → name from id
    assert conns["nomasx1"]["queries"][0]["name"] == "q3_select"

    # the migrated TOML round-trips through the v2 config loader (incl. the dialect map)
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    assert isinstance(reparsed.connectors["default"], SqlConnectorConfig)
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    assert q1.dialects == ["default", "oracle"]
    assert "FETCH FIRST 50 ROWS ONLY" in q1.sql_for("oracle")
    assert q1.sql_for("postgresql") == q1.default_sql  # no postgres variant → default
    assert next(q for q in reparsed.connectors["default"].queries if q.name == "delete_user_delete").writable is True


def test_migrate_sql_queries_dbtype_filter() -> None:
    out = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="oracle")
    names = [q["name"] for q in out["connectors"]["default"]["queries"]]
    # only the oracle variants survive the filter → plain-string SQL (one variant each)
    assert names == ["users_list_select", "twins_select"]
    assert isinstance(out["connectors"]["default"]["queries"][0]["sql"], str)


def test_migrate_sql_queries_connector_prefix() -> None:
    out = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="postgres", connector_prefix="v1_")
    assert "v1_default" in out["connectors"] and out["connectors"]["v1_default"]["pool"] == "v1_default"
    assert "v1_default" in out["pools"]


_CONNS = [
    {"conn_id": 10, "conn_label": "Acme API", "conn_url": "https://acme.example/api", "conn_user": "svc", "conn_password": "<encrypted>"},
    {"conn_id": 11, "conn_label": "Public", "conn_url": "https://pub.example", "conn_user": None, "conn_password": None},
]
_APIS = [
    {"api_id": 100, "api_label": "List Things", "api_source": None, "api_method": "GET", "api_url": "/things",
     "api_user": None, "api_password": None, "api_body": None, "api_conn_id": 10},
    {"api_id": 101, "api_label": "Create Thing", "api_source": None, "api_method": "POST", "api_url": "/things",
     "api_user": None, "api_password": None, "api_body": '{"name":"{{name}}"}', "api_conn_id": 10},
    {"api_id": 102, "api_label": "External", "api_source": None, "api_method": "GET",
     "api_url": "https://elsewhere.example/x", "api_user": None, "api_password": None, "api_body": None, "api_conn_id": None},
    {"api_id": 103, "api_label": "Unused conn endpoint", "api_source": None, "api_method": "GET", "api_url": "/y",
     "api_user": None, "api_password": None, "api_body": None, "api_conn_id": 11},  # conn 11 has this endpoint
]
_HEADERS = [{"api_id": 100, "hdr_id": 1, "hdr_key": "Accept", "hdr_value": "application/json"}]
_PARAMS = [{"api_id": 101, "map_id": 1, "map_var": "name", "map_value": "thing"}]


def test_migrate_api() -> None:
    out = migrate_api(_CONNS, _APIS, _HEADERS, _PARAMS)
    conns = out["connectors"]
    assert set(conns) == {"acme_api", "public", "legacy_api"}

    acme = conns["acme_api"]
    assert acme["type"] == "api" and acme["base_url"] == "https://acme.example/api"
    assert acme["auth_type"] == "basic" and acme["auth_username"] == "svc"
    assert acme["auth_password"] == "${MIGRATED_SECRET_ACME_API}"  # placeholder, not the v1-encrypted value
    eps = {e["name"]: e for e in acme["endpoints"]}
    assert eps["list_things"]["method"] == "GET" and eps["list_things"]["path"] == "/things"
    assert eps["list_things"]["headers"] == {"Accept": "application/json"}
    assert eps["create_thing"]["method"] == "POST" and eps["create_thing"]["body"] == '{"name":"{{name}}"}'
    assert eps["create_thing"]["params"] == [{"name": "name", "default": "thing"}]

    assert conns["public"]["auth_type"] == "none" and "auth_username" not in conns["public"]
    # the connectionless ly_api row goes into legacy_api with empty base_url + absolute path
    legacy = conns["legacy_api"]
    assert legacy["base_url"] == "" and legacy["endpoints"][0]["path"] == "https://elsewhere.example/x"

    # round-trips through the v2 config loader
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    assert isinstance(reparsed.connectors["acme_api"], ApiConnectorConfig)
    assert {e.name for e in reparsed.connectors["acme_api"].endpoints} == {"list_things", "create_thing"}


def test_migrate_api_drops_connectors_with_no_endpoints() -> None:
    # conn 12 has no ly_api rows → should not appear
    conns = [*_CONNS, {"conn_id": 12, "conn_label": "Empty", "conn_url": "https://empty.example", "conn_user": None, "conn_password": None}]
    out = migrate_api(conns, _APIS, _HEADERS, _PARAMS)
    assert "empty" not in out["connectors"]


def test_merge_connectors() -> None:
    a = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="postgres")
    b = migrate_api(_CONNS, _APIS, _HEADERS, _PARAMS)
    merged = merge_connectors(a, b)
    assert set(merged["pools"]) == {"default", "nomasx1"}
    assert set(merged["connectors"]) == {"default", "nomasx1", "acme_api", "public", "legacy_api"}
    parse_connectors(tomllib.loads(render_toml(merged)))  # still valid


# --------------------------------------------------------------------------- #
# DB readers (against a minimal v1 schema in SQLite)
# --------------------------------------------------------------------------- #

_V1_SCHEMA = [
    "CREATE TABLE ly_query (query_id INTEGER PRIMARY KEY, query_label TEXT, query_type TEXT)",
    "CREATE TABLE ly_qry_sql (query_id INTEGER, query_dbtype TEXT, query_crud TEXT, query_pool TEXT, query_sqlquery TEXT, query_orderby TEXT)",
    "CREATE TABLE ly_api_conn (conn_id INTEGER PRIMARY KEY, conn_label TEXT, conn_url TEXT, conn_user TEXT, conn_password TEXT)",
    "CREATE TABLE ly_api (api_id INTEGER PRIMARY KEY, api_label TEXT, api_source TEXT, api_method TEXT, api_url TEXT, api_user TEXT, api_password TEXT, api_body TEXT, api_conn_id INTEGER)",
    "CREATE TABLE ly_api_header (api_id INTEGER, hdr_id INTEGER, hdr_key TEXT, hdr_value TEXT)",
    "CREATE TABLE ly_api_params (api_id INTEGER, map_id INTEGER, map_var TEXT, map_value TEXT)",
]


async def _seed_v1(engine) -> None:
    async with engine.begin() as conn:
        for ddl in _V1_SCHEMA:
            await conn.execute(text(ddl))
        await conn.execute(
            text("INSERT INTO ly_query (query_id, query_label, query_type) VALUES (:i, :l, :t)"),
            [{"i": 1, "l": "Users List", "t": "TABLE"}, {"i": 2, "l": "Delete User", "t": "FORM"}],
        )
        # NB: bind the SQL text as a *value* (not part of the statement) so its `:id`
        # isn't parsed as a bindparam by text().
        await conn.execute(
            text("INSERT INTO ly_qry_sql (query_id, query_dbtype, query_crud, query_pool, query_sqlquery, query_orderby)"
                 " VALUES (:qid, :db, :crud, :pool, :sql, :ob)"),
            [
                {"qid": 1, "db": "postgres", "crud": "SELECT", "pool": "default", "sql": "SELECT * FROM ly_users", "ob": "usr_name"},
                {"qid": 2, "db": "postgres", "crud": "DELETE", "pool": "default", "sql": "DELETE FROM ly_users WHERE usr_id = :id", "ob": None},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_api_conn (conn_id, conn_label, conn_url, conn_user, conn_password) VALUES (:i, :l, :u, :usr, :p)"),
            [{"i": 10, "l": "Acme", "u": "https://acme.example", "usr": "svc", "p": "<enc>"}],
        )
        await conn.execute(
            text("INSERT INTO ly_api (api_id, api_label, api_source, api_method, api_url, api_user, api_password, api_body, api_conn_id)"
                 " VALUES (:i, :l, :src, :m, :u, :usr, :p, :b, :c)"),
            [{"i": 100, "l": "List", "src": None, "m": "GET", "u": "/things", "usr": None, "p": None, "b": None, "c": 10}],
        )
        await conn.execute(
            text("INSERT INTO ly_api_header (api_id, hdr_id, hdr_key, hdr_value) VALUES (:a, :h, :k, :v)"),
            [{"a": 100, "h": 1, "k": "Accept", "v": "application/json"}],
        )
        await conn.execute(
            text("INSERT INTO ly_api_params (api_id, map_id, map_var, map_value) VALUES (:a, :m, :var, :val)"),
            [{"a": 100, "m": 1, "var": "q", "val": "hello"}],
        )


@pytest_asyncio.fixture
async def v1_engine(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'v1.db'}")
    await _seed_v1(engine)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_sql_queries(v1_engine) -> None:
    queries, sql_rows = await read_sql_queries(v1_engine)
    assert {q["query_id"] for q in queries} == {1, 2}
    assert {r["query_crud"] for r in sql_rows} == {"SELECT", "DELETE"}
    out = migrate_sql_queries(queries, sql_rows)
    assert "default" in out["connectors"]
    parse_connectors(tomllib.loads(render_toml(out)))


@pytest.mark.asyncio
async def test_read_api(v1_engine) -> None:
    conns, apis, headers, params = await read_api(v1_engine)
    assert conns[0]["conn_label"] == "Acme" and apis[0]["api_url"] == "/things"
    assert headers[0]["hdr_key"] == "Accept" and params[0]["map_var"] == "q"
    out = migrate_api(conns, apis, headers, params)
    assert out["connectors"]["acme"]["endpoints"][0]["headers"] == {"Accept": "application/json"}
    parse_connectors(tomllib.loads(render_toml(out)))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _make_v1_db(tmp_path) -> str:
    import asyncio

    url = f"sqlite+aiosqlite:///{tmp_path / 'v1.db'}"

    async def go() -> None:
        engine = make_engine(url)
        await _seed_v1(engine)
        await engine.dispose()

    asyncio.run(go())
    return url


def test_cli_sql_to_file(tmp_path) -> None:
    url = _make_v1_db(tmp_path)
    out = tmp_path / "migrated.toml"
    assert migrate_main(["sql", "--source-url", url, "-o", str(out)]) == 0
    text_out = out.read_text()
    assert text_out.startswith("# migrated:")
    cfg = parse_connectors(tomllib.loads(text_out))  # comments + TOML both parse
    assert "default" in cfg.connectors and "default" in cfg.pools


def test_cli_all_to_stdout(tmp_path, capsys) -> None:
    url = _make_v1_db(tmp_path)
    assert migrate_main(["all", "--source-url", url]) == 0
    out = capsys.readouterr().out
    assert "fill in these placeholders" in out  # the migrated pool/secret stubs
    cfg = parse_connectors(tomllib.loads(out))
    assert {"default", "acme"} <= set(cfg.connectors)
