from __future__ import annotations

import tomllib

import pytest
import pytest_asyncio
from sqlalchemy import text

from liberty.connectors.config import ApiConnectorConfig, SqlConnectorConfig, parse_connectors
from liberty.menus import parse_menus
from liberty.migrate_cli import main as migrate_main
from liberty.migrations import (
    make_engine,
    merge_connectors,
    migrate_api,
    migrate_column_hints,
    migrate_dictionary,
    migrate_menus,
    migrate_pools,
    migrate_sql_queries,
    migrate_table_meta,
    read_api,
    read_applications,
    read_column_hints,
    read_dictionary,
    read_menus,
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


def test_migrate_sql_queries_filter_wrap() -> None:
    # a read query with a filter-flagged column gets wrapped: SELECT * FROM (<orig>) _flt WHERE …
    out = migrate_sql_queries(
        _QUERIES, _SQL_ROWS,
        column_hints={1: [{"name": "USR_ID", "filter": True}, {"name": "USR_NAME"}]},
    )
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    sql = by_name["users_list_select"]["sql"]
    assert isinstance(sql, dict)  # query 1 has two dialect variants → still a map
    inner = "SELECT usr_id, usr_name FROM ly_users WHERE usr_status = :status"
    assert sql["default"].startswith("SELECT * FROM (\n" + inner)
    assert "WHERE 1=1" in sql["default"]
    assert "CAST(:USR_ID AS VARCHAR(4000)) IS NULL" in sql["default"] and ":USR_ID_op" in sql["default"]
    assert "CAST(:USR_ID AS VARCHAR2(4000))" in sql["oracle"]  # the oracle variant uses VARCHAR2
    assert sql["default"].rstrip().endswith("ORDER BY usr_name")  # ORDER BY moved onto the outer query
    # a query with no filter columns is left alone (no wrapper)
    assert by_name["twins_select"]["sql"] == "SELECT 1 AS x"
    # write queries are never wrapped
    assert by_name["delete_user_delete"]["sql"] == "DELETE FROM ly_users WHERE usr_id = :id"
    # round-trips through the v2 loader
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    from liberty.connectors.base import find_bind_params
    assert {"status", "USR_ID", "USR_ID_op"} <= set(find_bind_params(q1.default_sql))


def test_migrate_table_meta() -> None:
    meta = migrate_table_meta(
        tables_rows=[
            {"tbl_query_id": 1, "tbl_label": "Security - Users", "tbl_auto_load": "Y"},
            {"tbl_query_id": 4, "tbl_label": "Twin Table", "tbl_auto_load": "N"},
            {"tbl_query_id": 7, "tbl_label": "", "tbl_auto_load": "Y"},  # no label, still auto-load
            {"tbl_query_id": None, "tbl_label": "Orphan", "tbl_auto_load": "Y"},  # no query → skipped
        ],
        dlg_frm_rows=[
            {"frm_query_id": 2, "frm_label": "Delete User"},
            {"frm_query_id": 1, "frm_label": "User Properties"},  # a table widget for q1 overrides this
        ],
    )
    assert meta[1] == {"description": "Security - Users", "auto_load": True}
    assert meta[4] == {"description": "Twin Table"}            # auto_load N → omitted
    assert meta[7] == {"auto_load": True}                       # label-less, flag only
    assert meta[2] == {"description": "Delete User"}            # form-only
    assert None not in meta


def test_migrate_sql_queries_with_table_meta() -> None:
    out = migrate_sql_queries(
        _QUERIES, _SQL_ROWS,
        table_meta={1: {"description": "Users List Screen", "auto_load": True}, 2: {"description": "Should be ignored — write query"}},
    )
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    assert by_name["users_list_select"]["description"] == "Users List Screen"
    assert by_name["users_list_select"]["auto_load"] is True
    # a write query (DELETE) gets no description/auto_load even if a table_meta entry exists
    assert "description" not in by_name["delete_user_delete"]
    assert "auto_load" not in by_name["delete_user_delete"]
    # round-trips through the v2 loader
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    assert q1.description == "Users List Screen" and q1.auto_load is True


# --------------------------------------------------------------------------- #
# Pools  (ly_applications → [pools.*])
# --------------------------------------------------------------------------- #

_APPLICATIONS = [
    {"apps_name": "Framework", "apps_pool": "default", "apps_dbtype": "postgres", "apps_jdbc": None,
     "apps_user": "liberty", "apps_password": "ENC:fw", "apps_host": "fw.example", "apps_port": 5432,
     "apps_database": "libnsx1", "apps_pool_min": 1, "apps_pool_max": 10},
    {"apps_name": "NOMASX1", "apps_pool": "nomasx1", "apps_dbtype": "postgres", "apps_jdbc": None,
     "apps_user": "nomasx1", "apps_password": "ENC:nx", "apps_host": "db.example", "apps_port": 5432,
     "apps_database": "nomasx1", "apps_pool_min": 2, "apps_pool_max": 20},
    {"apps_name": "NOMAJDE", "apps_pool": "nomajde", "apps_dbtype": "oracle", "apps_jdbc": None,
     "apps_user": "jde", "apps_password": None, "apps_host": "ora.example", "apps_port": 1521,
     "apps_database": "JDEPROD", "apps_pool_min": None, "apps_pool_max": None},
    {"apps_name": "ViaJdbc", "apps_pool": "viajdbc", "apps_dbtype": "postgres",
     "apps_jdbc": "jdbc:postgresql://jdbc-host:5444/jdbcdb", "apps_user": "u", "apps_password": "ENC:z",
     "apps_host": None, "apps_port": None, "apps_database": None, "apps_pool_min": None, "apps_pool_max": None},
    {"apps_name": "Incomplete", "apps_pool": "incomplete", "apps_dbtype": None, "apps_jdbc": None,
     "apps_user": "u", "apps_password": None, "apps_host": None, "apps_port": None, "apps_database": None,
     "apps_pool_min": None, "apps_pool_max": None},
]


def test_migrate_pools() -> None:
    out = migrate_pools(_APPLICATIONS)["pools"]
    # v1's `default` pool is skipped — v2 reserves [pools.default] for its own framework DB
    assert "default" not in out
    assert set(out) == {"nomasx1", "nomajde", "viajdbc", "incomplete"}

    nx = out["nomasx1"]
    assert nx["url"] == "postgresql+asyncpg://nomasx1:${MIGRATED_PW_NOMASX1}@db.example:5432/nomasx1"
    assert nx["dialect"] == "postgresql"
    assert nx["pool_pre_ping"] is True
    assert nx["pool_size"] == 20  # from apps_pool_max

    jde = out["nomajde"]
    assert jde["url"] == "oracle+oracledb://jde:${MIGRATED_PW_NOMAJDE}@ora.example:1521/?service_name=JDEPROD"
    assert jde["dialect"] == "oracle"
    assert "pool_size" not in jde  # apps_pool_max was None

    # apps_jdbc fallback when host/port/database aren't on the row
    assert out["viajdbc"]["url"] == "postgresql+asyncpg://u:${MIGRATED_PW_VIAJDBC}@jdbc-host:5444/jdbcdb"

    # nothing usable → keep the env-var stub, no explicit dialect
    assert out["incomplete"]["url"] == "${LIBERTY_DB_URL_INCOMPLETE}"
    assert "dialect" not in out["incomplete"]

    # round-trips through the v2 config loader
    parse_connectors(tomllib.loads(render_toml({"pools": out, "connectors": {}})))


def test_migrate_pools_prefix_and_overrides_stubs() -> None:
    out = migrate_pools([_APPLICATIONS[1]], connector_prefix="v1_")["pools"]
    assert "v1_nomasx1" in out and "${MIGRATED_PW_V1_NOMASX1}" in out["v1_nomasx1"]["url"]

    # merged after migrate_sql_queries → the real pool URL replaces the ${LIBERTY_DB_URL_*} stub
    sql_part = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="postgres")  # leaves [pools.nomasx1] a stub
    assert sql_part["pools"]["nomasx1"]["url"] == "${LIBERTY_DB_URL_NOMASX1}"
    merged = merge_connectors(sql_part, migrate_pools(_APPLICATIONS))
    assert merged["pools"]["nomasx1"]["url"].startswith("postgresql+asyncpg://")    # real one wins
    assert merged["pools"]["default"]["url"] == "${LIBERTY_DB_URL_DEFAULT}"          # default-pool stub kept (skipped by migrate_pools)
    parse_connectors(tomllib.loads(render_toml(merged)))


# --------------------------------------------------------------------------- #
# Column hints  (ly_tbl_col / ly_dlg_col → QueryDef.columns)  +  dictionary  (ly_dictionary → dictionary.toml)
# --------------------------------------------------------------------------- #

# Shape mirrors source.read_column_hints: col_dd_id references a ly_dictionary entry; col_label /
# col_type are per-column overrides of it (usually empty → label/format come from the dictionary).
_TBL_COLS = [
    {"query_id": 1, "col_target": "USR_ID", "col_dd_id": "USR_ID", "col_label": None, "col_seq": 1, "col_visible": "Y", "col_type": None, "col_id": 1},
    {"query_id": 1, "col_target": "USR_NAME", "col_dd_id": "USR_NAME", "col_label": None, "col_seq": 2, "col_visible": "Y", "col_type": None, "col_id": 2},
    {"query_id": 1, "col_target": "USR_PWD", "col_dd_id": "USR_PWD", "col_label": None, "col_seq": 3, "col_visible": "N", "col_type": None, "col_id": 3},  # col_visible 'N' → hidden
    {"query_id": 1, "col_target": "USR_LBL", "col_dd_id": "SOME_DD", "col_label": "Per-column override", "col_seq": 4, "col_visible": "Y", "col_type": "currency", "col_id": 4},  # col_dd_id ≠ name → dd ref; col_label/col_type override
    {"query_id": 1, "col_target": "USR_NAME", "col_dd_id": "USR_NAME", "col_label": "2nd widget", "col_seq": 1, "col_visible": "Y", "col_type": None, "col_id": 9},  # 2nd widget → dedup, first wins
    {"query_id": 3, "col_target": "F0101", "col_dd_id": "F0101", "col_label": None, "col_seq": 1, "col_visible": "Y", "col_type": None, "col_id": 1},
]
_DLG_COLS = [
    {"query_id": 2, "col_target": "USR_ID", "col_dd_id": "USR_ID", "col_label": None, "col_seq": 1, "col_visible": "1", "col_type": None, "col_id": 1},
    {"query_id": 1, "col_target": "USR_ID", "col_dd_id": "USR_ID", "col_label": "FROM DLG", "col_seq": 1, "col_visible": "Y", "col_type": None, "col_id": 1},  # query 1 USR_ID already from tbl → ignored
]


def test_migrate_column_hints() -> None:
    hints = migrate_column_hints(_TBL_COLS, _DLG_COLS)
    assert set(hints) == {1, 2, 3}
    assert [h["name"] for h in hints[1]] == ["USR_ID", "USR_NAME", "USR_PWD", "USR_LBL"]  # col_seq order; dedup'd; dlg ignored
    assert hints[1][0] == {"name": "USR_ID"}  # col_dd_id == name → no `dd`; label/format come from the dictionary
    assert hints[1][1] == {"name": "USR_NAME"}
    assert hints[1][2] == {"name": "USR_PWD", "hidden": True}  # col_visible 'N' → hidden
    assert hints[1][3] == {"name": "USR_LBL", "dd": "SOME_DD", "label": "Per-column override", "format": "currency"}  # dd ≠ name; col_label/col_type override
    assert hints[3] == [{"name": "F0101"}]
    assert hints[2] == [{"name": "USR_ID"}]  # only the form-field column


_DICTIONARY = [
    {"dd_id": "USR_NAME", "dd_label": "User Name", "dd_type": "text", "dd_rules": None, "dd_rules_values": None, "dd_default": None},
    {"dd_id": "USR_STATUS", "dd_label": "Status", "dd_type": "boolean", "dd_rules": "list", "dd_rules_values": "01,02", "dd_default": "01"},
    {"dd_id": "USR_DT", "dd_label": "Created", "dd_type": "date", "dd_rules": None, "dd_rules_values": None, "dd_default": None},
]
_DICTIONARY_L = [
    {"dd_id": "USR_NAME", "lng_id": "fr", "lng_label": "Nom d'utilisateur"},
    {"dd_id": "USR_STATUS", "lng_id": "fr", "lng_label": "Statut"},
    {"dd_id": "ONLY_TRANSLATED", "lng_id": "fr", "lng_label": "Que traduit"},  # no ly_dictionary row → kept as a translation-only entry
    {"dd_id": "USR_NAME", "lng_id": "", "lng_label": ""},  # blank → ignored
]


def test_migrate_dictionary() -> None:
    out = migrate_dictionary(_DICTIONARY, _DICTIONARY_L)
    assert out["default_language"] == "en" and "connectors" not in out
    e = out["entries"]
    assert set(e) == {"USR_NAME", "USR_STATUS", "USR_DT", "ONLY_TRANSLATED"}
    assert e["USR_NAME"] == {"label": "User Name", "l": {"fr": "Nom d'utilisateur"}}  # "text" dd_type → no format
    assert e["USR_STATUS"] == {"label": "Status", "format": "boolean", "rules": "list", "rules_values": "01,02", "default": "01", "l": {"fr": "Statut"}}
    assert e["USR_DT"] == {"label": "Created", "format": "date"}  # no translation row
    assert e["ONLY_TRANSLATED"] == {"l": {"fr": "Que traduit"}}  # translation-only
    # round-trips through the v2 dictionary loader, and `dd`-ref hints resolve against it
    from liberty.connectors.dictionary import parse_dictionary
    d = parse_dictionary(tomllib.loads(render_toml(out)))
    assert d.resolve("USR_NAME", "fr") == ("Nom d'utilisateur", None)
    assert d.resolve("USR_STATUS", None) == ("Status", "boolean")
    assert d.resolve("USR_DT", "fr") == ("Created", "date")  # no fr → falls back to the default label
    # --connector → nested under [connectors.<name>.entries.*] (so apps don't clash on a dd_id)
    out2 = migrate_dictionary(_DICTIONARY, _DICTIONARY_L, connector_name="nomasx1")
    assert "entries" not in out2 and set(out2["connectors"]["nomasx1"]["entries"]) == set(e)
    d2 = parse_dictionary(tomllib.loads(render_toml(out2)))
    assert d2.resolve("USR_NAME", "fr", connector="nomasx1") == ("Nom d'utilisateur", None)
    assert d2.resolve("USR_NAME", "fr") == (None, None)  # nothing at the top level


def test_migrate_sql_queries_with_column_hints() -> None:
    out = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="postgres",
                              column_hints={3: [{"name": "F0101", "label": "Address Book"}]})
    q3 = out["connectors"]["nomasx1"]["queries"][0]  # query 3 lives on the nomasx1 connector
    assert q3["columns"] == [{"name": "F0101", "label": "Address Book"}]
    # a DELETE query gets no hints even if passed — display hints only apply to result sets
    out2 = migrate_sql_queries(_QUERIES, _SQL_ROWS, dbtype="postgres", column_hints={2: [{"name": "X"}]})
    by_name = {q["name"]: q for q in out2["connectors"]["default"]["queries"]}
    assert "columns" not in by_name["delete_user_delete"]
    parse_connectors(tomllib.loads(render_toml(out)))  # round-trips through the v2 config loader


def test_migrate_sql_queries_rest_crud_verbs() -> None:
    # v1's query_crud is a REST verb: GET = read, POST/PUT/DELETE = write
    queries = [{"query_id": 1, "query_label": "Things", "query_type": "TABLE"}]
    sql_rows = [
        {"query_id": 1, "query_dbtype": "generic", "query_crud": "GET", "query_pool": "app",
         "query_sqlquery": "SELECT id, name FROM things", "query_orderby": "id"},
        {"query_id": 1, "query_dbtype": "generic", "query_crud": "POST", "query_pool": "app",
         "query_sqlquery": "INSERT INTO things (name) VALUES (:name)", "query_orderby": None},
    ]
    out = migrate_sql_queries(queries, sql_rows, column_hints={1: [{"name": "name", "label": "Name"}]})
    by_name = {q["name"]: q for q in out["connectors"]["app"]["queries"]}
    # GET → read: ORDER BY appended, no `writable`, gets the column hints
    assert by_name["things_get"]["sql"].endswith("ORDER BY id")
    assert "writable" not in by_name["things_get"]
    assert by_name["things_get"]["columns"] == [{"name": "name", "label": "Name"}]
    # POST → write: `writable = true`, no ORDER BY, no column hints
    assert by_name["things_post"]["writable"] is True
    assert "ORDER BY" not in by_name["things_post"]["sql"]
    assert "columns" not in by_name["things_post"]
    parse_connectors(tomllib.loads(render_toml(out)))


_CONNS = [
    {"conn_id": 10, "conn_label": "Acme API", "conn_url": "https://acme.example/api", "conn_user": "svc", "conn_password": "ENC:dGVzdA=="},
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
    assert acme["auth_password"] == "ENC:dGVzdA=="  # the v1 ENC: value, carried over verbatim (v2 decrypts at runtime)
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
    "CREATE TABLE ly_applications (apps_name TEXT, apps_pool TEXT, apps_dbtype TEXT, apps_jdbc TEXT, apps_user TEXT, apps_password TEXT, apps_host TEXT, apps_port INTEGER, apps_database TEXT, apps_pool_min INTEGER, apps_pool_max INTEGER)",
    "CREATE TABLE ly_dictionary (dd_id TEXT PRIMARY KEY, dd_label TEXT, dd_type TEXT, dd_rules TEXT, dd_rules_values TEXT, dd_default TEXT)",
    "CREATE TABLE ly_dictionary_l (dd_id TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_enum (enum_id INTEGER PRIMARY KEY, enum_label TEXT)",
    "CREATE TABLE ly_enum_val (enum_id INTEGER, val_enum TEXT, val_label TEXT)",
    "CREATE TABLE ly_enum_val_l (enum_id INTEGER, val_enum TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_lookup (lkp_id INTEGER PRIMARY KEY, lkp_description TEXT, lkp_query_id INTEGER, lkp_dd_id TEXT, lkp_dd_label TEXT, lkp_dd_group TEXT)",
    "CREATE TABLE ly_tables (tbl_id INTEGER PRIMARY KEY, tbl_query_id INTEGER, tbl_label TEXT, tbl_auto_load TEXT)",
    "CREATE TABLE ly_tbl_col (tbl_id INTEGER, col_id INTEGER, col_seq INTEGER, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT, col_filter TEXT)",
    "CREATE TABLE ly_dlg_frm (frm_id INTEGER PRIMARY KEY, dlg_id INTEGER, frm_query_id INTEGER, frm_label TEXT)",
    "CREATE TABLE ly_dlg_col (frm_id INTEGER, col_id INTEGER, tab_id INTEGER, col_seq INTEGER, col_component TEXT, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT)",
    "CREATE TABLE ly_menus (menu_seq_ukid TEXT PRIMARY KEY, menu_parent_id TEXT, menu_child_id TEXT, menu_component TEXT, menu_component_id INTEGER, menu_label TEXT, menu_level INTEGER)",
    "CREATE TABLE ly_menus_l (lng_id TEXT, lng_seq_ukid TEXT, lng_label TEXT)",
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
            text("INSERT INTO ly_applications (apps_name, apps_pool, apps_dbtype, apps_jdbc, apps_user, apps_password, apps_host, apps_port, apps_database, apps_pool_min, apps_pool_max)"
                 " VALUES (:n, :p, :db, :j, :u, :pw, :h, :port, :d, :mn, :mx)"),
            [
                {"n": "Framework", "p": "default", "db": "postgres", "j": None, "u": "liberty", "pw": "ENC:fw", "h": "fw.example", "port": 5432, "d": "libnsx1", "mn": 1, "mx": 10},
                {"n": "NOMASX1", "p": "nomasx1", "db": "postgres", "j": None, "u": "nomasx1", "pw": "ENC:nx", "h": "db.example", "port": 5432, "d": "nomasx1", "mn": 2, "mx": 20},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_dictionary (dd_id, dd_label, dd_type, dd_rules, dd_rules_values, dd_default) VALUES (:i, :l, :t, :r, :rv, :d)"),
            [
                {"i": "USR_ID", "l": "User ID", "t": "number", "r": None, "rv": None, "d": None},
                {"i": "USR_NAME", "l": "User Name", "t": "text", "r": None, "rv": None, "d": None},
                {"i": "USR_PWD", "l": "Password", "t": "text", "r": None, "rv": None, "d": None},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_dictionary_l (dd_id, lng_id, lng_label) VALUES (:i, :lng, :lab)"),
            [{"i": "USR_NAME", "lng": "fr", "lab": "Nom d'utilisateur"}],
        )
        # Display rules on a few of the dictionary entries — exercises the BOOLEAN/ENUM/LOOKUP shapes.
        await conn.execute(
            text("UPDATE ly_dictionary SET dd_rules = :r, dd_rules_values = :v WHERE dd_id = :i"),
            [
                {"i": "USR_ID", "r": "LOOKUP", "v": "1"},     # → ly_lookup.lkp_id = 1
                {"i": "USR_NAME", "r": "ENUM", "v": "1"},     # → ly_enum.enum_id = 1
                {"i": "USR_PWD", "r": "PASSWORD", "v": None}, # form-layer — no display rule emitted
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_enum (enum_id, enum_label) VALUES (:i, :l)"),
            [{"i": 1, "l": "User Status"}],
        )
        await conn.execute(
            text("INSERT INTO ly_enum_val (enum_id, val_enum, val_label) VALUES (:i, :v, :l)"),
            [{"i": 1, "v": "A", "l": "Active"}, {"i": 1, "v": "I", "l": "Inactive"}],
        )
        await conn.execute(
            text("INSERT INTO ly_enum_val_l (enum_id, val_enum, lng_id, lng_label) VALUES (:i, :v, :lng, :lab)"),
            [{"i": 1, "v": "A", "lng": "fr", "lab": "Actif"}],
        )
        await conn.execute(
            text("INSERT INTO ly_lookup (lkp_id, lkp_description, lkp_query_id, lkp_dd_id, lkp_dd_label, lkp_dd_group) "
                 "VALUES (:i, :d, :q, :v, :l, :g)"),
            [{"i": 1, "d": "Users list", "q": 1, "v": "USR_ID", "l": "USR_NAME", "g": None}],
        )
        await conn.execute(
            text("INSERT INTO ly_tables (tbl_id, tbl_query_id, tbl_label, tbl_auto_load) VALUES (:i, :q, :l, :al)"),
            [{"i": 5, "q": 1, "l": "Users", "al": "Y"}],
        )
        await conn.execute(
            text("INSERT INTO ly_tbl_col (tbl_id, col_id, col_seq, col_dd_id, col_label, col_target, col_type, col_visible, col_filter)"
                 " VALUES (:t, :c, :s, :dd, :lab, :tgt, :ty, :v, :f)"),
            [
                {"t": 5, "c": 1, "s": 1, "dd": "USR_ID", "lab": None, "tgt": "USR_ID", "ty": "number", "v": "Y", "f": "Y"},
                {"t": 5, "c": 2, "s": 2, "dd": None, "lab": "User Name", "tgt": "USR_NAME", "ty": "text", "v": "Y", "f": "N"},
                {"t": 5, "c": 3, "s": 3, "dd": None, "lab": "Password", "tgt": "USR_PWD", "ty": "password", "v": "N", "f": None},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_frm (frm_id, dlg_id, frm_query_id, frm_label) VALUES (:i, :d, :q, :l)"),
            [{"i": 7, "d": 1, "q": 2, "l": "Delete Form"}],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_col (frm_id, col_id, tab_id, col_seq, col_component, col_dd_id, col_label, col_target, col_type, col_visible)"
                 " VALUES (:f, :c, :ta, :s, :cm, :dd, :lab, :tgt, :ty, :v)"),
            [{"f": 7, "c": 1, "ta": 1, "s": 1, "cm": "input", "dd": None, "lab": "Id", "tgt": "USR_ID", "ty": "integer", "v": "Y"}],
        )
        await conn.execute(
            text("INSERT INTO ly_menus (menu_seq_ukid, menu_parent_id, menu_child_id, menu_component, menu_component_id, menu_label, menu_level)"
                 " VALUES (:seq, :par, :chl, :cmp, :cid, :lab, :lvl)"),
            [
                {"seq": "100001.", "par": "0", "chl": "100001.", "cmp": None, "cid": None, "lab": "Security", "lvl": 1},
                # FormsTable → ly_tables.tbl_id=5 → query 1 ("Users List", crud SELECT) → users_list_select
                {"seq": "100001.100001.", "par": "100001.", "chl": "100001.100001.", "cmp": "FormsTable", "cid": 5, "lab": "Users", "lvl": 2},
                # a Dashboard node — not a query screen → a folder placeholder with no children
                {"seq": "100002.", "par": "0", "chl": "100002.", "cmp": "Dashboard", "cid": 1, "lab": "Overview", "lvl": 1},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_menus_l (lng_id, lng_seq_ukid, lng_label) VALUES (:lng, :seq, :lab)"),
            [{"lng": "fr", "seq": "100001.100001.", "lab": "Utilisateurs"}],
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


@pytest.mark.asyncio
async def test_read_applications(v1_engine) -> None:
    apps = await read_applications(v1_engine)
    assert {a["apps_pool"] for a in apps} == {"default", "nomasx1"}
    pools = migrate_pools(apps)["pools"]
    assert "default" not in pools  # v2 reserves [pools.default]
    assert pools["nomasx1"]["url"] == "postgresql+asyncpg://nomasx1:${MIGRATED_PW_NOMASX1}@db.example:5432/nomasx1"


@pytest.mark.asyncio
async def test_read_column_hints(v1_engine) -> None:
    tbl_cols, dlg_cols = await read_column_hints(v1_engine)
    assert {(r["query_id"], r["col_target"]) for r in tbl_cols} == {(1, "USR_ID"), (1, "USR_NAME"), (1, "USR_PWD")}
    assert {(r["query_id"], r["col_target"]) for r in dlg_cols} == {(2, "USR_ID")}
    hints = migrate_column_hints(tbl_cols, dlg_cols)
    assert [h["name"] for h in hints[1]] == ["USR_ID", "USR_NAME", "USR_PWD"]
    assert hints[1][0] == {"name": "USR_ID", "filter": True, "format": "number"}  # col_dd_id == name → no `dd`; col_filter 'Y'
    assert hints[1][2] == {"name": "USR_PWD", "label": "Password", "hidden": True, "format": "password"}  # col_visible 'N', col_filter null
    assert hints[2] == [{"name": "USR_ID", "label": "Id", "format": "integer"}]


@pytest.mark.asyncio
async def test_read_dictionary(v1_engine) -> None:
    dict_rows, dict_l_rows = await read_dictionary(v1_engine)
    assert {r["dd_id"] for r in dict_rows} == {"USR_ID", "USR_NAME", "USR_PWD"}
    assert {(r["dd_id"], r["lng_id"]) for r in dict_l_rows} == {("USR_NAME", "fr")}
    out = migrate_dictionary(dict_rows, dict_l_rows)
    from liberty.connectors.dictionary import parse_dictionary
    d = parse_dictionary(tomllib.loads(render_toml(out)))
    assert d.resolve("USR_NAME", "fr") == ("Nom d'utilisateur", None)
    assert d.resolve("USR_ID", None) == ("User ID", "number")


@pytest.mark.asyncio
async def test_read_dictionary_rules_and_migrate(v1_engine) -> None:
    from liberty.connectors.dictionary import parse_dictionary
    from liberty.migrations import read_dictionary_rules
    dict_rows, dict_l_rows = await read_dictionary(v1_engine)
    enum_rows, enum_val_rows, enum_val_l_rows, lookup_rows, sql_rows = await read_dictionary_rules(v1_engine)
    assert [r["enum_id"] for r in enum_rows] == [1]
    assert {(r["enum_id"], r["val_enum"]) for r in enum_val_rows} == {(1, "A"), (1, "I")}
    assert {r["lkp_id"] for r in lookup_rows} == {1}
    out = migrate_dictionary(
        dict_rows, dict_l_rows, enum_rows, enum_val_rows, enum_val_l_rows, lookup_rows, sql_rows,
        connector_name="db",
    )
    d = parse_dictionary(tomllib.loads(render_toml(out)))
    sec = d.connectors["db"]
    # enum migrated, with the fr translation on the Active value
    assert sec.enums["1"].label == "User Status"
    vals = {v.value: v for v in sec.enums["1"].values}
    assert vals["A"].label == "Active" and vals["A"].label_for("fr") == "Actif"
    assert vals["I"].label_for("fr") == "Inactive"  # no fr translation → falls back to the default
    # lookup migrated, with lkp_query_id=1 → the read-variant name migrate_sql_queries gives that query
    assert sec.lookups["1"].query == "users_list_select"
    assert sec.lookups["1"].value == "USR_ID" and sec.lookups["1"].label == "USR_NAME"
    # the entries' rules round-trip; resolve_rule returns the right wire shape
    usr_id_rule = d.resolve_rule(sec.entries["USR_ID"], connector="db", language="en")
    assert usr_id_rule == {
        "kind": "lookup", "connector": "db", "query": "users_list_select", "value": "USR_ID", "label": "USR_NAME",
    }
    usr_name_rule = d.resolve_rule(sec.entries["USR_NAME"], connector="db", language="fr")
    assert usr_name_rule == {
        "kind": "enum",
        "values": [{"value": "A", "label": "Actif"}, {"value": "I", "label": "Inactive"}],
    }
    # PASSWORD is a form-layer rule — not a display transform; resolve_rule returns None
    assert d.resolve_rule(sec.entries["USR_PWD"], connector="db") is None


@pytest.mark.asyncio
async def test_read_menus(v1_engine) -> None:
    menu_rows, menu_l_rows, tables_rows, dlg_frm_rows, sql_rows = await read_menus(v1_engine)
    assert {r["menu_seq_ukid"] for r in menu_rows} == {"100001.", "100001.100001.", "100002."}
    assert {(r["lng_seq_ukid"], r["lng_id"]) for r in menu_l_rows} == {("100001.100001.", "fr")}
    assert {r["tbl_id"] for r in tables_rows} == {5} and {r["query_id"] for r in sql_rows} == {1, 2}
    out = migrate_menus(menu_rows, menu_l_rows, tables_rows, dlg_frm_rows, sql_rows, app_name="nomasx1")
    items = {it["id"]: it for it in out["menus"]["nomasx1"]["items"]}
    assert set(items) == {"security", "users", "overview"}
    assert items["security"].get("type") is None and "parent" not in items["security"]  # top-level folder
    assert items["users"] == {
        "id": "users", "label": "Users", "parent": "security",
        "type": "query", "target": "users_list_select", "connector": "default", "l": {"fr": "Utilisateurs"},
    }  # FormsTable → ly_tables 5 → query 1 (SELECT, pool 'default') → its migrated name + connector ('default' ≠ app 'nomasx1')
    assert items["overview"].get("type") is None  # Dashboard → unresolved → folder placeholder
    # the menu target lines up with what migrate_sql_queries actually emits
    queries, sql_q = await read_sql_queries(v1_engine)
    sql_conn = migrate_sql_queries(queries, sql_q)["connectors"]["default"]
    assert "users_list_select" in {q["name"] for q in sql_conn["queries"]}
    # and the whole thing round-trips through the menus schema
    m = parse_menus(tomllib.loads(render_toml(out)))
    assert [it.id for it in m.menus["nomasx1"].items] == ["security", "users", "overview"]


@pytest.mark.asyncio
async def test_read_applications_column_hints_dictionary_menus_missing_tables(tmp_path) -> None:
    from liberty.migrations import read_dictionary_rules
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'no_meta.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE ly_query (query_id INTEGER PRIMARY KEY)"))
    try:
        assert await read_applications(engine) == []           # no ly_applications → []
        assert await read_column_hints(engine) == ([], [])     # no ly_tbl_col/ly_dlg_col → ([], [])
        assert await read_dictionary(engine) == ([], [])       # no ly_dictionary/ly_dictionary_l → ([], [])
        assert await read_menus(engine) == ([], [], [], [], [])  # no ly_menus/… → all empty
        assert await read_dictionary_rules(engine) == ([], [], [], [], [])  # no ly_enum/ly_lookup/… → all empty
    finally:
        await engine.dispose()


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
    # ly_applications scaffolded [pools.nomasx1] with a real URL + a password placeholder;
    # v1's `default` pool was skipped, so [pools.default] is still the env-var stub.
    assert "nomasx1" in cfg.pools
    assert "postgresql+asyncpg://nomasx1:${MIGRATED_PW_NOMASX1}@db.example:5432/nomasx1" in text_out
    assert "${LIBERTY_DB_URL_DEFAULT}" in text_out  # the default pool kept its env-var stub
    # ly_tbl_col → the migrated SELECT carries column display hints
    sql_conn = cfg.connectors["default"]
    assert isinstance(sql_conn, SqlConnectorConfig)
    q1 = next(q for q in sql_conn.queries if q.name == "users_list_select")
    assert [c.name for c in q1.columns] == ["USR_ID", "USR_NAME", "USR_PWD"]
    assert q1.columns[2].hidden is True and q1.columns[1].label == "User Name"


def test_cli_all_to_stdout(tmp_path, capsys) -> None:
    url = _make_v1_db(tmp_path)
    assert migrate_main(["all", "--source-url", url]) == 0
    out = capsys.readouterr().out
    assert "fill in these placeholders" in out  # the migrated pool/secret stubs
    assert "MIGRATED_PW_" in out  # the scaffolded pool's password placeholder
    assert "liberty-migrate dictionary" in out  # column hints reference the shared dictionary
    cfg = parse_connectors(tomllib.loads(out))
    assert {"default", "acme"} <= set(cfg.connectors)
    assert "nomasx1" in cfg.pools


def test_cli_dictionary(tmp_path) -> None:
    from liberty.connectors.dictionary import parse_dictionary
    url = _make_v1_db(tmp_path)
    out = tmp_path / "dictionary.toml"
    assert migrate_main(["dictionary", "--source-url", url, "-o", str(out)]) == 0
    txt = out.read_text()
    assert txt.startswith("# migrated:") and "top-level dictionary field" in txt
    d = parse_dictionary(tomllib.loads(txt))  # comments + TOML both parse
    assert d.resolve("USR_NAME", "fr") == ("Nom d'utilisateur", None)
    assert d.resolve("USR_ID", None) == ("User ID", "number")
    # --connector nomasx1 → nested under [connectors.nomasx1.entries.*]
    out2 = tmp_path / "dictionary_ns.toml"
    assert migrate_main(["dictionary", "--source-url", url, "--connector", "nomasx1", "-o", str(out2)]) == 0
    txt2 = out2.read_text()
    assert "[connectors.nomasx1] dictionary field" in txt2
    d2 = parse_dictionary(tomllib.loads(txt2))
    assert d2.resolve("USR_NAME", "fr", connector="nomasx1") == ("Nom d'utilisateur", None)
    assert d2.resolve("USR_NAME", "fr") == (None, None)  # nothing at the top level


def test_cli_menu(tmp_path) -> None:
    url = _make_v1_db(tmp_path)
    out = tmp_path / "menus.toml"
    assert migrate_main(["menu", "--source-url", url, "--connector", "nomasx1", "-o", str(out)]) == 0
    txt = out.read_text()
    assert txt.startswith("# migrated:") and "[menus.nomasx1]" in txt
    assert "overview" in txt  # the Dashboard placeholder is flagged in the header
    m = parse_menus(tomllib.loads(txt))  # comments + TOML both parse against the menus schema
    items = {it.id: it for it in m.menus["nomasx1"].items}
    assert set(items) == {"security", "users", "overview"}
    assert items["users"].type == "query" and items["users"].target == "users_list_select"
    assert items["users"].parent == "security" and items["users"].l == {"fr": "Utilisateurs"}
