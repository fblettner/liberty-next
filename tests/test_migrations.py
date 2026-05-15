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
    attach_actions_to_screens,
    migrate_actions,
    migrate_column_visibility,
    migrate_dictionary,
    migrate_drill_filter_columns,
    migrate_menus,
    migrate_pools,
    migrate_context_menus,
    migrate_screens,
    migrate_sql_queries,
    migrate_key_columns,
    migrate_table_filters,
    migrate_table_meta,
    read_api,
    read_applications,
    read_column_hints,
    read_dictionary,
    read_menus,
    read_column_conditions,
    read_screens,
    read_sql_queries,
    read_table_filters,
    render_toml,
    slugify,
)
from liberty.screens import parse_screens


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


def test_migrate_key_columns() -> None:
    keys = migrate_key_columns(
        # rows arrive in col_seq order (the readers ORDER BY col_seq)
        tbl_col_rows=[
            {"query_id": 1, "col_target": "USR_APPS_ID", "col_key": "Y"},
            {"query_id": 1, "col_target": "USR_NAME", "col_key": "N"},  # not a key → dropped
            {"query_id": 1, "col_target": "USR_ID", "col_key": "Y"},
            {"query_id": None, "col_target": "X", "col_key": "Y"},  # no query → skipped
        ],
        dlg_col_rows=[{"query_id": 1, "col_target": "USR_ID", "col_key": "Y"}],  # dup of a table-widget key → ignored
    )
    assert keys == {1: ["USR_APPS_ID", "USR_ID"]}  # given (col_seq) order, deduped; non-key columns dropped


def test_migrate_sql_queries_put_where_rewrite() -> None:
    queries = [{"query_id": 1, "query_label": "Users", "query_type": "TABLE"}]
    sql_rows = [
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "GET", "query_pool": "default",
         "query_sqlquery": "SELECT USR_APPS_ID, USR_ID, USR_NAME FROM SECURITY_USERS", "query_orderby": None},
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "PUT", "query_pool": "default",
         "query_sqlquery": "UPDATE SECURITY_USERS SET USR_NAME = :USR_NAME, USR_ID = :USR_ID "
                           "WHERE USR_APPS_ID = :USR_APPS_ID AND USR_ID = :USR_ID", "query_orderby": None},
    ]
    out = migrate_sql_queries(queries, sql_rows, key_columns={1: ["USR_APPS_ID", "USR_ID"]})
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    put_sql = by_name["users_put"]["sql"]
    # the SET clause keeps the new values; the WHERE binds the key columns to :<col>_ORIGINAL
    assert "SET USR_NAME = :USR_NAME, USR_ID = :USR_ID" in put_sql
    assert "WHERE USR_APPS_ID = :USR_APPS_ID_ORIGINAL AND USR_ID = :USR_ID_ORIGINAL" in put_sql
    # the read query is untouched
    assert by_name["users_get"]["sql"] == "SELECT USR_APPS_ID, USR_ID, USR_NAME FROM SECURITY_USERS"
    assert by_name["users_get"]["key_columns"] == ["USR_APPS_ID", "USR_ID"]  # surfaced for the import match-by-key
    assert "key_columns" not in by_name["users_put"]  # only the read query carries it
    from liberty.connectors.base import find_bind_params
    binds = set(find_bind_params(put_sql))
    assert {"USR_NAME", "USR_ID", "USR_APPS_ID_ORIGINAL", "USR_ID_ORIGINAL"} <= binds
    assert "USR_APPS_ID" not in binds  # only the _ORIGINAL form survives in the WHERE


def test_migrate_sql_queries_simplify_upsert() -> None:
    from liberty.connectors.base import detect_statement_type, find_bind_params
    queries = [{"query_id": 1, "query_label": "Users", "query_type": "TABLE"},
               {"query_id": 2, "query_label": "Roles", "query_type": "TABLE"}]
    sql_rows = [
        # PostgreSQL upsert _post → plain INSERT (the ON CONFLICT … tail dropped)
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "POST", "query_pool": "default",
         "query_sqlquery": "INSERT INTO USERS (ID, NAME) VALUES (:ID, :NAME)\nON CONFLICT (ID) DO UPDATE SET NAME = :NAME",
         "query_orderby": None},
        # Oracle MERGE _post → plain INSERT rebuilt from the WHEN NOT MATCHED clause
        {"query_id": 2, "query_dbtype": "oracle", "query_crud": "POST", "query_pool": "default",
         "query_sqlquery": "MERGE INTO ROLES t\nUSING (SELECT :ID AS ID, :LABEL AS LABEL FROM dual) src\n"
                           "ON (t.ID = src.ID)\nWHEN MATCHED THEN UPDATE SET t.LABEL = src.LABEL\n"
                           "WHEN NOT MATCHED THEN INSERT (ID, LABEL) VALUES (src.ID, src.LABEL)",
         "query_orderby": None},
    ]
    out = migrate_sql_queries(queries, sql_rows)
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    pg = by_name["users_post"]["sql"]
    assert "ON CONFLICT" not in pg and detect_statement_type(pg) == "INSERT"
    assert set(find_bind_params(pg)) == {"ID", "NAME"}
    ora = by_name["roles_post"]["sql"]
    assert "MERGE" not in ora and detect_statement_type(ora) == "INSERT"
    assert "INSERT INTO ROLES" in ora and set(find_bind_params(ora)) == {"ID", "LABEL"}
    # both still round-trip + stay writable
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    assert all(q.writable for q in reparsed.connectors["default"].queries)


def test_migrate_sql_queries_upsert_put_to_update() -> None:
    from liberty.connectors.base import detect_statement_type, find_bind_params
    queries = [{"query_id": 1, "query_label": "Users", "query_type": "TABLE"},
               {"query_id": 2, "query_label": "Roles", "query_type": "TABLE"}]
    sql_rows = [
        # v1 registered a PG upsert under the PUT crud → collapse to a plain UPDATE (key cols → :<col>_ORIGINAL)
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "PUT", "query_pool": "default",
         "query_sqlquery": "INSERT INTO USERS (APPS_ID, ID, NAME) VALUES (:APPS_ID, :ID, :NAME)\n"
                           "ON CONFLICT (APPS_ID, ID) DO UPDATE SET NAME = :NAME", "query_orderby": None},
        # an Oracle MERGE under PUT → plain UPDATE built from the WHEN MATCHED + ON clauses
        {"query_id": 2, "query_dbtype": "oracle", "query_crud": "PUT", "query_pool": "default",
         "query_sqlquery": "MERGE INTO ROLES t\nUSING (SELECT :ID AS ID, :LABEL AS LABEL FROM dual) src\n"
                           "ON (t.ID = src.ID)\nWHEN MATCHED THEN UPDATE SET t.LABEL = src.LABEL\n"
                           "WHEN NOT MATCHED THEN INSERT (ID, LABEL) VALUES (src.ID, src.LABEL)",
         "query_orderby": None},
    ]
    out = migrate_sql_queries(queries, sql_rows)
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    pg = by_name["users_put"]["sql"]
    assert detect_statement_type(pg) == "UPDATE" and "ON CONFLICT" not in pg
    assert "SET NAME = :NAME" in pg
    assert set(find_bind_params(pg)) == {"NAME", "APPS_ID_ORIGINAL", "ID_ORIGINAL"}  # key cols → _ORIGINAL in the WHERE
    ora = by_name["roles_put"]["sql"]
    assert detect_statement_type(ora) == "UPDATE" and "MERGE" not in ora
    assert "UPDATE ROLES" in ora and "SET LABEL = :LABEL" in ora
    assert set(find_bind_params(ora)) == {"LABEL", "ID_ORIGINAL"}
    assert all(q.writable for q in parse_connectors(tomllib.loads(render_toml(out))).connectors["default"].queries)


def test_migrate_sql_queries_filter_wrap() -> None:
    # a read query with a filter-flagged column gets wrapped: SELECT * FROM (<orig>) lib_flt WHERE …
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
    assert ") lib_flt\n" in sql["default"] and "_flt" not in sql["default"].replace("lib_flt", "")  # alias must start with a letter (Oracle)
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
            # Slice 5 — tbl_audit='Y' + tbl_db_name → audit_table for the writable companions
            {"tbl_query_id": 10, "tbl_label": "Apps", "tbl_db_name": "settings_apps", "tbl_audit": "Y"},
            # tbl_audit='Y' but no tbl_db_name → no audit (operator can fill in via the builder)
            {"tbl_query_id": 11, "tbl_label": "Anon", "tbl_db_name": "", "tbl_audit": "Y"},
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
    assert meta[10] == {"description": "Apps", "audit_table": "AUD_SETTINGS_APPS"}  # tbl_audit → AUD_<UPPER(db_name)>
    assert meta[11] == {"description": "Anon"}                  # no db_name → no audit
    assert 12 not in meta                                       # nothing for the orphan
    assert None not in meta


def test_migrate_table_filters() -> None:
    out = migrate_table_filters(
        tbl_filter_rows=[
            {"query_id": 1, "col_target": "USR_NAME", "src": "USR_ID", "tgt": "UN_REF", "val": None},
            {"query_id": 1, "col_target": "USR_NAME", "src": "USR_ID", "tgt": "UN_REF", "val": None},  # dup → dropped
            {"query_id": 1, "col_target": "ROLE", "src": "APPS_ID", "tgt": "ROL_APPS_ID", "val": None},
            {"query_id": 1, "col_target": "ROLE", "src": "DEPT", "tgt": "ROL_DEPT", "val": None},      # 2nd dep on the same col
            {"query_id": None, "col_target": "X", "src": "Y", "tgt": "Z", "val": None},                # no query → skipped
            {"query_id": 3, "col_target": "C", "src": "", "tgt": "Z", "val": None},                    # blank source → skipped
        ],
        dlg_filter_rows=[
            {"query_id": 1, "col_target": "USR_NAME", "src": "USR_ID", "tgt": "OTHER", "val": None},   # table widget wins → ignored
            {"query_id": 2, "col_target": "F", "src": "G", "tgt": "H", "val": None},                   # form-only col → kept
        ],
    )
    assert out[1]["USR_NAME"] == [{"source": "USR_ID", "column": "UN_REF"}]
    assert out[1]["ROLE"] == [{"source": "APPS_ID", "column": "ROL_APPS_ID"}, {"source": "DEPT", "column": "ROL_DEPT"}]
    assert out[2]["F"] == [{"source": "G", "column": "H"}]
    assert 3 not in out and None not in out


def test_migrate_sql_queries_filter_from() -> None:
    out = migrate_sql_queries(
        _QUERIES, _SQL_ROWS,
        column_hints={1: [{"name": "USR_ID", "filter": True}, {"name": "USR_NAME", "filter": True}]},
        column_filters={1: {"USR_NAME": [{"source": "USR_ID", "column": "UN_REF"}]},
                        2: {"USR_ID": [{"source": "X", "column": "Y"}]}},  # query 2 is a write → no columns → no-op
    )
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    cols = {c["name"]: c for c in by_name["users_list_select"]["columns"]}
    assert cols["USR_NAME"]["filter_from"] == [{"source": "USR_ID", "column": "UN_REF"}]
    assert "filter_from" not in cols["USR_ID"]  # no entry for it
    # round-trips through the v2 loader
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    un = next(c for c in q1.columns if c.name == "USR_NAME")
    assert [{"source": d.source, "column": d.column} for d in un.filter_from] == [{"source": "USR_ID", "column": "UN_REF"}]
    assert "delete_user_delete" in by_name  # write query unaffected


def test_migrate_column_visibility() -> None:
    tbl_cols = [
        {"query_id": 1, "col_target": "SY_COL", "col_dd_id": "SY", "col_cdn_id": None},  # no cdn — also provides the SY→SY_COL field index
        {"query_id": 1, "col_target": "A", "col_dd_id": "A", "col_cdn_id": 10},   # one field, EQUAL + EMPTY
        {"query_id": 1, "col_target": "B", "col_dd_id": "B", "col_cdn_id": 11},   # two fields → both emitted, AND-ed
        {"query_id": 1, "col_target": "C", "col_dd_id": "C", "col_cdn_id": 13},   # bad operator → skipped
        {"query_id": 1, "col_target": "D", "col_dd_id": "D", "col_cdn_id": 14},   # cdn has only EMPTY → no conds
        {"query_id": 1, "col_target": "E", "col_dd_id": "E", "col_cdn_id": None},  # no cdn → absent
    ]
    params = [
        {"cdn_id": 10, "cdn_dd_id": "SY", "cdn_operator": "EQUAL", "cdn_value": "OBJECTS"},
        {"cdn_id": 10, "cdn_dd_id": "SY", "cdn_operator": "EMPTY", "cdn_value": None},
        {"cdn_id": 11, "cdn_dd_id": "SY", "cdn_operator": "EQUAL", "cdn_value": "ALIAS"},
        {"cdn_id": 11, "cdn_dd_id": "SY", "cdn_operator": "EQUAL", "cdn_value": "OTHERS"},
        {"cdn_id": 11, "cdn_dd_id": "RT", "cdn_operator": "EQUAL", "cdn_value": "1"},
        {"cdn_id": 11, "cdn_dd_id": "RT", "cdn_operator": "EMPTY", "cdn_value": None},
        {"cdn_id": 13, "cdn_dd_id": "X", "cdn_operator": "NOT_EQUAL", "cdn_value": "9"},  # unsupported op → cdn 13 "bad"
        {"cdn_id": 14, "cdn_dd_id": "RT", "cdn_operator": "EMPTY", "cdn_value": None},
    ]
    out = migrate_column_visibility(tbl_cols, (), params)
    # A: SY resolves via the col_dd_id index → screen column SY_COL
    assert out[1]["A"] == [{"field": "SY_COL", "value": ["OBJECTS"]}]
    # B: two AND-ed conditions, in predicate order; RT has no matching col_dd_id → stays "RT"
    assert out[1]["B"] == [{"field": "SY_COL", "value": ["ALIAS", "OTHERS"]}, {"field": "RT", "value": ["1"]}]
    # C skipped (bad op), D has only an EMPTY predicate → no conds, E never had a cdn
    assert "C" not in out[1] and "D" not in out[1] and "E" not in out[1]


def test_migrate_sql_queries_visible_when() -> None:
    out = migrate_sql_queries(
        _QUERIES, _SQL_ROWS,
        column_hints={1: [{"name": "USR_ID"}, {"name": "USR_NAME"}]},
        column_visibility={1: {"USR_NAME": [{"field": "USR_ID", "value": ["42"]}]},
                           2: {"USR_ID": [{"field": "X", "value": ["y"]}]}},  # write query → no-op
    )
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    cols = {c["name"]: c for c in by_name["users_list_select"]["columns"]}
    assert cols["USR_NAME"]["visible_when"] == [{"field": "USR_ID", "value": ["42"]}]
    assert "visible_when" not in cols["USR_ID"]
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    un = next(c for c in q1.columns if c.name == "USR_NAME")
    assert [r.as_dict() for r in un.visible_when_rules] == [{"field": "USR_ID", "value": ["42"]}]


def test_migrate_sql_queries_with_table_meta() -> None:
    out = migrate_sql_queries(
        _QUERIES, _SQL_ROWS,
        table_meta={
            1: {"description": "Users List Screen", "auto_load": True},
            # description on a write-only query gets ignored; audit_table propagates to the write variants
            2: {"description": "Should be ignored — write query", "audit_table": "AUD_LY_USERS"},
        },
    )
    by_name = {q["name"]: q for q in out["connectors"]["default"]["queries"]}
    assert by_name["users_list_select"]["description"] == "Users List Screen"
    assert by_name["users_list_select"]["auto_load"] is True
    # a write query (DELETE) gets no description/auto_load even if a table_meta entry exists
    assert "description" not in by_name["delete_user_delete"]
    assert "auto_load" not in by_name["delete_user_delete"]
    # Slice 5 — the write companion picks up `audit = "AUD_LY_USERS"` from table_meta; the
    # read companion gets *no* audit (it never mutates anything to mirror).
    assert by_name["delete_user_delete"]["audit"] == "AUD_LY_USERS"
    assert "audit" not in by_name["users_list_select"]
    # round-trips through the v2 loader
    reparsed = parse_connectors(tomllib.loads(render_toml(out)))
    q1 = next(q for q in reparsed.connectors["default"].queries if q.name == "users_list_select")
    qd = next(q for q in reparsed.connectors["default"].queries if q.name == "delete_user_delete")
    assert q1.description == "Users List Screen" and q1.auto_load is True
    assert qd.audit == "AUD_LY_USERS" and q1.audit is None


# --------------------------------------------------------------------------- #
# Pools  (ly_applications → [pools.*])
# --------------------------------------------------------------------------- #

_APPLICATIONS = [
    {"apps_name": "Framework", "apps_pool": "default", "apps_dbtype": "postgres", "apps_jdbc": None,
     "apps_user": "liberty", "apps_password": "ENC:fw", "apps_host": "fw.example", "apps_port": 5432,
     "apps_database": "libnsx1", "apps_pool_min": 1, "apps_pool_max": 10, "apps_limit": 1000},
    {"apps_name": "NOMASX1", "apps_pool": "nomasx1", "apps_dbtype": "postgres", "apps_jdbc": None,
     "apps_user": "nomasx1", "apps_password": "ENC:nx", "apps_host": "db.example", "apps_port": 5432,
     "apps_database": "nomasx1", "apps_pool_min": 2, "apps_pool_max": 20, "apps_limit": 5000},
    {"apps_name": "NOMAJDE", "apps_pool": "nomajde", "apps_dbtype": "oracle", "apps_jdbc": None,
     "apps_user": "jde", "apps_password": None, "apps_host": "ora.example", "apps_port": 1521,
     "apps_database": "JDEPROD", "apps_pool_min": None, "apps_pool_max": None, "apps_limit": None},
    {"apps_name": "ViaJdbc", "apps_pool": "viajdbc", "apps_dbtype": "postgres",
     "apps_jdbc": "jdbc:postgresql://jdbc-host:5444/jdbcdb", "apps_user": "u", "apps_password": "ENC:z",
     "apps_host": None, "apps_port": None, "apps_database": None, "apps_pool_min": None, "apps_pool_max": None, "apps_limit": 0},
    {"apps_name": "Incomplete", "apps_pool": "incomplete", "apps_dbtype": None, "apps_jdbc": None,
     "apps_user": "u", "apps_password": None, "apps_host": None, "apps_port": None, "apps_database": None,
     "apps_pool_min": None, "apps_pool_max": None, "apps_limit": None},
]


def test_migrate_pools() -> None:
    db_schemas = [
        {"sch_pool": "nomasx1", "sch_name": "DTA", "sch_target": "PRODDTA"},
        {"sch_pool": "nomasx1", "sch_name": "SY", "sch_target": "SY920"},
        {"sch_pool": "jde_extra", "sch_name": "CTL", "sch_target": "JDECTL"},  # pool not in ly_applications → stub
    ]
    out = migrate_pools(_APPLICATIONS, db_schemas=db_schemas)["pools"]
    # v1's `default` pool is skipped — v2 reserves [pools.default] for its own framework DB
    assert "default" not in out
    assert set(out) == {"nomasx1", "nomajde", "viajdbc", "incomplete", "jde_extra"}

    nx = out["nomasx1"]
    # the password is a separate field (kept out of the URL); v1's `ENC:` value carried over verbatim
    assert nx["url"] == "postgresql+asyncpg://nomasx1@db.example:5432/nomasx1"
    assert nx["password"] == "ENC:nx"  # = apps_password
    assert nx["dialect"] == "postgresql"
    assert nx["pool_pre_ping"] is True
    assert nx["pool_size"] == 2 and nx["max_overflow"] == 18  # apps_pool_min=2 / apps_pool_max=20 → 2 + 18 burst
    assert nx["max_rows"] == 5000  # from apps_limit
    assert nx["schemas"] == {"DTA": "PRODDTA", "SY": "SY920"}  # ly_db_schema → #SCHEMA.<name># map

    jde = out["nomajde"]
    assert jde["url"] == "oracle+oracledb://jde@ora.example:1521/?service_name=JDEPROD"
    assert jde["password"] == "${MIGRATED_PW_NOMAJDE}"  # apps_password was None → env-var stub
    assert jde["dialect"] == "oracle"
    assert "pool_size" not in jde and "max_overflow" not in jde  # apps_pool_min/max were None
    assert "max_rows" not in jde and "schemas" not in jde  # apps_limit / ly_db_schema had none
    assert "max_rows" not in out["viajdbc"]  # apps_limit was 0 → not set

    # apps_jdbc fallback when host/port/database aren't on the row
    assert out["viajdbc"]["url"] == "postgresql+asyncpg://u@jdbc-host:5444/jdbcdb"
    assert out["viajdbc"]["password"] == "ENC:z"

    # nothing usable → keep the env-var stub, no explicit dialect, no separate password
    assert out["incomplete"]["url"] == "${LIBERTY_DB_URL_INCOMPLETE}"
    assert "dialect" not in out["incomplete"] and "password" not in out["incomplete"]

    # ly_db_schema rows for a pool with no ly_applications row → a stub pool carrying just the schemas
    assert out["jde_extra"]["url"] == "${LIBERTY_DB_URL_JDE_EXTRA}" and out["jde_extra"]["schemas"] == {"CTL": "JDECTL"}

    # round-trips through the v2 config loader
    parse_connectors(tomllib.loads(render_toml({"pools": out, "connectors": {}})))


def test_migrate_pools_prefix_and_overrides_stubs() -> None:
    out = migrate_pools([_APPLICATIONS[1]], connector_prefix="v1_")["pools"]
    assert "v1_nomasx1" in out and out["v1_nomasx1"]["url"].startswith("postgresql+asyncpg://nomasx1@")
    assert out["v1_nomasx1"]["password"] == "ENC:nx"

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


def test_migrate_column_hints_extra_filter_cols() -> None:
    """``extra_filter_cols`` promotes drill-destination columns to ``filter = True`` — both for
    columns that already have a hint (the flag is OR-ed on) and for columns the migrator didn't
    otherwise know about (a minimal ``{name, filter}`` row is appended so ``_wrap_with_filters``
    still binds them). Case-insensitive match on the column name."""
    extras = {1: ["USR_PWD", "EXTRA_COL"], 3: ["f0101"]}  # USR_PWD already a hint, EXTRA_COL new; lowercase f0101 still matches
    hints = migrate_column_hints(_TBL_COLS, _DLG_COLS, extra_filter_cols=extras)
    # query 1: existing USR_PWD now flagged; EXTRA_COL appended with just {name, filter}; the other rows unchanged.
    by_name = {h["name"]: h for h in hints[1]}
    assert by_name["USR_PWD"] == {"name": "USR_PWD", "hidden": True, "filter": True}
    assert by_name["EXTRA_COL"] == {"name": "EXTRA_COL", "filter": True}
    assert by_name["USR_ID"] == {"name": "USR_ID"}  # untouched
    # query 3: f0101 (case-insensitive) flagged the existing F0101 — no duplicate row.
    assert hints[3] == [{"name": "F0101", "filter": True}]


def test_migrate_drill_filter_columns() -> None:
    """Each ly_ctx_filters row resolves through (ctx_id, val_id) → ly_ctx_val → val_component(_id)
    → destination query_id, then the row's flt_target becomes a filter column on that destination.
    Both ``DD`` (dynamic) and ``VALUE`` (literal) flt_types behave the same: in either case the
    URL drill (NavigateAction → /sql/c/q?COL=v) needs a filter slot on the destination to land in."""
    val_rows = [
        # FormsTable drill (val_component_id = tbl_id 100 → tbl_query_id 20)
        {"ctx_id": 1, "val_id": 1, "val_label": "Display Roles",
         "val_component": "FormsTable", "val_component_id": 100},
        # FormsDialog drill (val_component_id = frm_id 7 → frm_query_id 21)
        {"ctx_id": 1, "val_id": 2, "val_label": "Display Properties",
         "val_component": "FormsDialog", "val_component_id": 7},
        # Orphan — unknown tbl_id → no destination → filters under it are dropped
        {"ctx_id": 1, "val_id": 3, "val_label": "Orphan",
         "val_component": "FormsTable", "val_component_id": 999},
    ]
    filter_rows = [
        # ctx 1 / val 1 — two DD binds → both flt_targets are columns on query 20
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "RLU_APPS_ID", "flt_source": "USR_APPS_ID"},
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "RLU_USER_ID", "flt_source": "USR_ID"},
        # ctx 1 / val 2 — one VALUE literal → STATUS is a column on query 21
        {"ctx_id": 1, "val_id": 2, "flt_type": "VALUE", "flt_target": "STATUS", "flt_value": "A"},
        # dup ctx 1 / val 1 / RLU_APPS_ID — deduped (case-insensitive on flt_target)
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "rlu_apps_id", "flt_source": "USR_APPS_ID"},
        # missing flt_target → dropped
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": None, "flt_source": "X"},
        # orphan val (no destination) → dropped
        {"ctx_id": 1, "val_id": 3, "flt_type": "DD", "flt_target": "X", "flt_source": "Y"},
    ]
    tables_rows = [
        {"tbl_id": 100, "tbl_db_name": "security_assignments", "tbl_query_id": 20},
    ]
    dlg_frm_rows = [
        {"frm_id": 7, "frm_query_id": 21},
    ]
    out = migrate_drill_filter_columns(val_rows, filter_rows, tables_rows=tables_rows, dlg_frm_rows=dlg_frm_rows)
    assert out == {20: ["RLU_APPS_ID", "RLU_USER_ID"], 21: ["STATUS"]}


def test_migrate_drill_filter_columns_resolves_dd_to_col_target() -> None:
    """v1's ``flt_target`` is the dictionary key (``col_dd_id``), not always the SQL column.
    On a query like ``sod_summary_users_get`` the destination column is ``CFD_APPS_ID`` with
    ``col_dd_id = APPS_ID`` — a drill referencing ``APPS_ID`` must be translated to
    ``CFD_APPS_ID`` so the migrated wrapper binds the real column, not a phantom one. (The bug
    surfaced in production as 502s on the dashboard's SOD widgets.)"""
    val_rows = [
        {"ctx_id": 1, "val_id": 1, "val_label": "Drill",
         "val_component": "FormsTable", "val_component_id": 100},
    ]
    filter_rows = [
        # v1 stored `flt_target = "APPS_ID"` (the dictionary key); the destination's real column
        # is `CFD_APPS_ID`. Without translation the migrator emits `lib_flt.APPS_ID` and the SQL
        # parser barfs.
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "APPS_ID", "flt_source": "USR_APPS_ID"},
        # A flt_target that's also a real column name on the destination — should stay as-is
        # (the col_target match wins over the dd lookup).
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "CFD_USER_ID", "flt_source": "USR_ID"},
        # An flt_target with no matching column or dd — fall through with a warning (no crash).
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "UNKNOWN", "flt_source": "X"},
    ]
    tables_rows = [{"tbl_id": 100, "tbl_query_id": 20}]
    # Destination's column hints — CFD_APPS_ID has dd_id = APPS_ID (the v1 dictionary key).
    tbl_cols = [
        {"query_id": 20, "col_target": "CFD_APPS_ID", "col_dd_id": "APPS_ID", "col_seq": 1},
        {"query_id": 20, "col_target": "CFD_USER_ID", "col_dd_id": "USR_ID", "col_seq": 2},
    ]
    out = migrate_drill_filter_columns(
        val_rows, filter_rows, tables_rows=tables_rows, tbl_col_rows=tbl_cols,
    )
    # APPS_ID → CFD_APPS_ID (via dd lookup); CFD_USER_ID kept (real column); UNKNOWN kept (warned)
    assert out == {20: ["CFD_APPS_ID", "CFD_USER_ID", "UNKNOWN"]}


def test_migrate_drill_filter_columns_dd_resolution_first_occurrence_wins() -> None:
    """If two columns share the same ``col_dd_id``, the lower ``col_seq`` wins. Pathological but
    possible — the v1 convention is one dd per column, so this is rare."""
    val_rows = [
        {"ctx_id": 1, "val_id": 1, "val_component": "FormsTable", "val_component_id": 100},
    ]
    filter_rows = [
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "APPS_ID", "flt_source": "USR_APPS_ID"},
    ]
    tables_rows = [{"tbl_id": 100, "tbl_query_id": 20}]
    tbl_cols = [
        # col_seq 1: USR_APPS_ID has dd APPS_ID — picked first
        {"query_id": 20, "col_target": "USR_APPS_ID", "col_dd_id": "APPS_ID", "col_seq": 1},
        # col_seq 2: SAME dd — ignored (first occurrence wins)
        {"query_id": 20, "col_target": "CFD_APPS_ID", "col_dd_id": "APPS_ID", "col_seq": 2},
    ]
    out = migrate_drill_filter_columns(val_rows, filter_rows, tables_rows=tables_rows, tbl_col_rows=tbl_cols)
    assert out == {20: ["USR_APPS_ID"]}


def test_migrate_drill_filter_columns_end_to_end() -> None:
    """End-to-end check: ``migrate_drill_filter_columns`` → ``migrate_column_hints`` →
    ``migrate_sql_queries`` wraps the destination SQL with ``:RLU_APPS_ID``/``:RLU_USER_ID``
    binds, so the URL drill emitted by ``migrate_context_menus`` actually filters server-side."""
    val_rows = [
        {"ctx_id": 1, "val_id": 1, "val_label": "Display Roles",
         "val_component": "FormsTable", "val_component_id": 100},
    ]
    filter_rows = [
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "RLU_APPS_ID", "flt_source": "USR_APPS_ID"},
        {"ctx_id": 1, "val_id": 1, "flt_type": "DD", "flt_target": "RLU_USER_ID", "flt_source": "USR_ID"},
    ]
    tables_rows = [
        {"tbl_id": 100, "tbl_db_name": "security_assignments", "tbl_query_id": 20},
    ]
    drill = migrate_drill_filter_columns(val_rows, filter_rows, tables_rows=tables_rows)
    assert drill == {20: ["RLU_APPS_ID", "RLU_USER_ID"]}
    # The destination had no v1 column hints at all — the drill-filter pass still emits hints so
    # the SQL gets wrapped. (Real-world libnsx1 case for `security_assignments_get`.)
    hints = migrate_column_hints((), (), extra_filter_cols=drill)
    assert hints[20] == [
        {"name": "RLU_APPS_ID", "filter": True},
        {"name": "RLU_USER_ID", "filter": True},
    ]
    queries = [{"query_id": 20, "query_label": "Assignments"}]
    sql_rows = [{
        "query_id": 20, "query_crud": "GET", "query_pool": "nomasx1", "query_dbtype": "postgres",
        "query_sqlquery": "SELECT * FROM assignments", "query_orderby": None,
    }]
    out = migrate_sql_queries(queries, sql_rows, column_hints=hints)
    sql = out["connectors"]["nomasx1"]["queries"][0]["sql"]
    assert ":RLU_APPS_ID" in sql and ":RLU_USER_ID" in sql  # _wrap_with_filters bound both
    assert "lib_flt" in sql  # wrapped, not raw


def test_migrate_sql_queries_drops_filter_for_missing_columns() -> None:
    """A `filter = true` hint that names a column the SQL doesn't expose generates a
    ``lib_flt.<missing>`` WHERE clause that errors at run time (the bug behind the dashboard's
    SOD widget 502s). The migrator now strips the flag with a logged warning so the wrapper
    only binds real columns. The hint itself stays — only `filter` goes away — so the column
    still gets its label/format/etc."""
    queries = [{"query_id": 20, "query_label": "Foo"}]
    sql_rows = [{
        "query_id": 20, "query_crud": "GET", "query_pool": "nomasx1", "query_dbtype": "postgres",
        # SELECT exposes JDEO_APPS_ID + CPT_ID + USERS_COUNT — NOT LUSR_APPS_ID.
        "query_sqlquery": "SELECT JDEO_APPS_ID, CPT_ID, USERS_COUNT FROM some_table",
        "query_orderby": None,
    }]
    column_hints = {20: [
        # `filter = true` on a column not in the SELECT → should be dropped
        {"name": "LUSR_APPS_ID", "dd": "APPS_ID", "filter": True},
        # `filter = true` on a real column → kept
        {"name": "JDEO_APPS_ID", "dd": "APPS_ID", "filter": True},
        # No `filter` flag at all → untouched (case-insensitive match against the SELECT)
        {"name": "cpt_id"},
    ]}
    out = migrate_sql_queries(queries, sql_rows, column_hints=column_hints)
    q = out["connectors"]["nomasx1"]["queries"][0]
    cols = {c["name"]: c for c in q["columns"]}
    # The broken filter is gone; the other one remains; the third is unchanged.
    assert "filter" not in cols["LUSR_APPS_ID"]
    assert cols["JDEO_APPS_ID"].get("filter") is True
    # The wrapped SQL only references the real column in its WHERE.
    sql = q["sql"]
    assert ":JDEO_APPS_ID" in sql and ":LUSR_APPS_ID" not in sql
    assert "lib_flt.JDEO_APPS_ID" in sql and "lib_flt.LUSR_APPS_ID" not in sql


def test_outermost_select_columns_handles_nested_selects() -> None:
    """The parser used by `_drop_filter_for_missing_cols` walks the SQL respecting paren depth —
    nested SELECTs inside subqueries / CASE expressions / function args don't fool it. Output
    column names are picked from the *outermost* SELECT's column list."""
    from liberty.migrations.v1 import _outermost_select_columns
    sql = """
        SELECT
            t.A,
            t.B AS RENAMED,
            (SELECT COUNT(*) FROM other) AS CNT,
            CASE WHEN t.X > 0 THEN 1 ELSE 0 END FLAG
        FROM main t
        WHERE t.Z > 0
    """
    cols = _outermost_select_columns(sql)
    assert cols is not None
    assert cols == {"A", "RENAMED", "CNT", "FLAG"}


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


def test_migrate_dictionary_password_rule_sets_password_format() -> None:
    """v1's ``dd_rules = "PASSWORD"`` marks a credential column. v2's frontend keys the masked
    widget (PasswordInput, "leave blank to keep" placeholder) off the entry's
    ``format = "password"`` — not ``rules`` — so the migrator sets both. Without this the
    dictionary entry would carry only the rule and the dialog would render a plain text input,
    leaking the ENC: blob into the form (the bug security_applications hit before this fix)."""
    rows = [
        {"dd_id": "APPS_PASSWORD", "dd_label": "Password", "dd_type": None, "dd_rules": "PASSWORD",
         "dd_rules_values": None, "dd_default": None},
        # Existing `format` from a non-empty dd_type wins — only fill in when format is unset.
        {"dd_id": "PWD_TEXTAREA", "dd_label": "Notes", "dd_type": "textarea", "dd_rules": "PASSWORD",
         "dd_rules_values": None, "dd_default": None},
    ]
    out = migrate_dictionary(rows, ())
    e = out["entries"]
    assert e["APPS_PASSWORD"] == {"label": "Password", "format": "password", "rules": "PASSWORD"}
    # `dd_type` set → keep it (the operator picked a specific format); rule still carried.
    assert e["PWD_TEXTAREA"] == {"label": "Notes", "format": "textarea", "rules": "PASSWORD"}


def test_migrate_dictionary_lookup_params() -> None:
    """v1's ly_dictionary_filters (flt_type='VALUE') → DictionaryEntry.lookup_params per entry.
    Several entries can reuse the same lookup (e.g. UDC) with different SY/RT pairs — that's the
    whole point of this table: AT1 needs SY=01,RT=ST while LMSG needs SY=H00,RT=LM, even though
    both call the same `Get UDC Description` lookup."""
    dictionary = [
        {"dd_id": "AT1",  "dd_label": "Activity Type 1", "dd_rules": "LOOKUP", "dd_rules_values": "1"},
        {"dd_id": "LMSG", "dd_label": "Last Message",    "dd_rules": "LOOKUP", "dd_rules_values": "1"},
        {"dd_id": "USR_NAME", "dd_label": "User Name"},
    ]
    filters = [
        {"dd_id": "AT1",  "flt_target": "SY", "flt_value": "01",  "flt_type": "VALUE"},
        {"dd_id": "AT1",  "flt_target": "RT", "flt_value": "ST",  "flt_type": "VALUE"},
        {"dd_id": "LMSG", "flt_target": "SY", "flt_value": "H00", "flt_type": "VALUE"},
        {"dd_id": "LMSG", "flt_target": "RT", "flt_value": "LM",  "flt_type": "VALUE"},
        # non-VALUE types (FIELD/DD/…) are dynamic, bound at form/table runtime — skipped here.
        {"dd_id": "AT1", "flt_target": "FOO", "flt_value": "x", "flt_type": "FIELD"},
        # blank/missing values are ignored — no point emitting an empty binding.
        {"dd_id": "AT1", "flt_target": "BAR", "flt_value": "",  "flt_type": "VALUE"},
        # entries with no ly_dictionary row are tolerated (their filters end up unused).
        {"dd_id": "GHOST", "flt_target": "X", "flt_value": "y", "flt_type": "VALUE"},
    ]
    out = migrate_dictionary(dictionary, dictionary_filters_rows=filters)
    e = out["entries"]
    assert e["AT1"]["lookup_params"] == {"SY": "01", "RT": "ST"}
    assert e["LMSG"]["lookup_params"] == {"SY": "H00", "RT": "LM"}
    assert "lookup_params" not in e["USR_NAME"]
    # resolve_rule passes the params through alongside the lookup ref
    from liberty.connectors.dictionary import EnumDef, LookupDef, parse_dictionary
    d = parse_dictionary({
        "entries": e,
        "lookups": {"1": LookupDef(query="get_udc_description_get", value="KY", label="DL01").model_dump()},
    })
    at1 = d.find_entry("AT1")
    assert at1 is not None
    rule = d.resolve_rule(at1)
    assert rule == {"kind": "lookup", "connector": None, "query": "get_udc_description_get",
                    "value": "KY", "label": "DL01", "params": {"SY": "01", "RT": "ST"}}
    _ = EnumDef  # silence unused-import warning when the helper isn't needed for an assertion path


def test_migrate_sql_queries_lookup_param_wrap() -> None:
    """A read query used as a lookup target (ly_lookup.lkp_query_id) gets its SQL wrapped with
    `WHERE (:P IS NULL OR <col> = :P)` per declared param, and the params are declared on
    QueryDef.params so the SQL connector binds them. NULL/blank → no filter."""
    from liberty.migrations import migrate_lookup_param_names
    queries = [{"query_id": 7, "query_label": "Get UDC Description", "query_type": "LOOKUP"}]
    sql_rows = [{
        "query_id": 7, "query_dbtype": "postgres", "query_crud": "GET", "query_pool": "ds",
        "query_sqlquery": "SELECT DRDL01 DL01, DRKY KY, DRRT RT, DRSY SY FROM F0005",
    }]
    lookup_rows = [{"lkp_id": "1", "lkp_query_id": 7}]
    lookup_params_rows = [
        {"lkp_id": "1", "dd_id": "SY"},
        {"lkp_id": "1", "dd_id": "RT"},
    ]
    by_qid = migrate_lookup_param_names(lookup_rows, lookup_params_rows)
    assert by_qid == {7: ["SY", "RT"]}
    out = migrate_sql_queries(queries, sql_rows, dbtype="postgres", lookup_params=by_qid)
    q = out["connectors"]["ds"]["queries"][0]
    sql = q["sql"]
    assert "lib_lkp" in sql and ":SY" in sql and ":RT" in sql
    assert "IS NULL OR" in sql           # NULL bind → match every row
    assert q["params"] == [{"name": "SY"}, {"name": "RT"}]
    assert "writable" not in q           # read query → omitted (default False)


def test_migrate_dictionary_lookup_param_names() -> None:
    """v1's ly_lkp_params declares which `:placeholder` params each lookup's query needs (one
    row per (lkp_id, dd_id)). The migration attaches the ordered list to the lookup as
    `params` so the builder UI can auto-surface the matching input fields."""
    lookups = [
        {"lkp_id": "1", "lkp_description": "Get UDC", "lkp_query_id": 7, "lkp_dd_id": "KY", "lkp_dd_label": "DL01"},
        {"lkp_id": "2", "lkp_description": "No params", "lkp_query_id": 8, "lkp_dd_id": "C", "lkp_dd_label": "L"},
    ]
    # ly_qry_sql rows so the migrator can resolve lkp_query_id → a v2 query name.
    sql_rows = [
        {"query_id": 7, "query_label": "Get UDC Description", "query_crud": "GET", "query_pool": "ds"},
        {"query_id": 8, "query_label": "Other", "query_crud": "GET", "query_pool": "ds"},
    ]
    lkp_params = [
        {"lkp_id": "1", "dd_id": "SY"},
        {"lkp_id": "1", "dd_id": "RT"},
        # duplicates are filtered out (preserved order: first occurrence wins)
        {"lkp_id": "1", "dd_id": "SY"},
    ]
    out = migrate_dictionary([], lookup_rows=lookups, sql_rows=sql_rows, lookup_params_rows=lkp_params)
    assert out["lookups"]["1"]["params"] == ["SY", "RT"]
    # Lookup 2 has no rows → `params` not emitted (Pydantic default_factory).
    assert "params" not in out["lookups"]["2"]
    from liberty.connectors.dictionary import parse_dictionary
    d = parse_dictionary(tomllib.loads(render_toml(out)))
    assert d.lookups["1"].params == ["SY", "RT"] and d.lookups["2"].params == []


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
# migrate_screens — Phase 6 slice 1 (ly_tables + ly_dlg_* → screens.toml)
# --------------------------------------------------------------------------- #

# Re-usable sql_rows that name a few CRUD verbs migrate_screens needs to resolve. Real
# v1 data uses REST verbs (GET/PUT/POST/DELETE) — the v2 query name is built from the **raw**
# v1 crud verbatim, so it matches whatever migrate_sql_queries emits in connectors.toml.
_SCR_SQL = [
    {"query_id": 10, "query_label": "Users", "query_crud": "GET", "query_pool": "nomasx1", "query_dbtype": "postgres",
     "query_sqlquery": "SELECT * FROM users", "query_orderby": None},
    {"query_id": 10, "query_label": "Users", "query_crud": "PUT", "query_pool": "nomasx1", "query_dbtype": "postgres",
     "query_sqlquery": "UPDATE users SET …", "query_orderby": None},
    {"query_id": 10, "query_label": "Users", "query_crud": "POST", "query_pool": "nomasx1", "query_dbtype": "postgres",
     "query_sqlquery": "INSERT INTO users …", "query_orderby": None},
    {"query_id": 10, "query_label": "Users", "query_crud": "DELETE", "query_pool": "nomasx1", "query_dbtype": "postgres",
     "query_sqlquery": "DELETE FROM users", "query_orderby": None},
]


def test_migrate_screens_no_dialog() -> None:
    """A ly_tables row with no tbl_frm_id → screen carries the read/update/insert/delete refs
    but no ``dialog`` (read-only / grid-edit only) and the flag wiring round-trips."""
    table_rows = [
        {"tbl_id": 1, "tbl_db_name": "security_users", "tbl_query_id": 10, "tbl_label": "Users",
         "tbl_editable": "Y", "tbl_uploadable": "N", "tbl_audit": "Y", "tbl_auto_load": "Y", "tbl_frm_id": None},
    ]
    out = migrate_screens(table_rows, sql_rows=_SCR_SQL, app_name="nomasx1")
    assert list(out["screens"]) == ["nomasx1"]
    screens = out["screens"]["nomasx1"]
    assert list(screens) == ["security_users"]
    s = screens["security_users"]
    assert s == {
        "id": "security_users",
        "label": "Users",
        "description": "Users",
        "read_query": "users_get",
        "update_query": "users_put",
        "insert_query": "users_post",
        "delete_query": "users_delete",
        "auto_load": True,
        "audit": True,
    }
    # Round-trips through the screens schema.
    parse_screens(out)


def test_migrate_screens_with_dialog() -> None:
    """A screen with a dialog: tabs from ly_dlg_tab (translations from _l), fields from
    ly_dlg_col, and per-field ``lookup_param_binds`` from ly_dlg_filters (both VALUE
    and DD flavours of ParamBind)."""
    table_rows = [
        {"tbl_id": 5, "tbl_db_name": "security_users", "tbl_query_id": 10, "tbl_label": "Users",
         "tbl_editable": "Y", "tbl_uploadable": "Y", "tbl_audit": None, "tbl_auto_load": "N", "tbl_frm_id": 7},
    ]
    frm_rows = [{"frm_id": 7, "dlg_id": 1, "frm_query_id": 10, "frm_label": "User"}]
    tab_rows = [
        {"frm_id": 7, "tab_id": 1, "tab_seq": 1, "tab_label": "General", "tab_cols": 2,
         "tab_disable_add": "N", "tab_disable_edit": "N"},
        {"frm_id": 7, "tab_id": 2, "tab_seq": 2, "tab_label": "Audit", "tab_cols": None,
         "tab_disable_add": "Y", "tab_disable_edit": "Y"},
    ]
    tab_l_rows = [
        {"frm_id": 7, "tab_id": 1, "lng_id": "fr", "lng_label": "Général"},
    ]
    col_rows = [
        {"frm_id": 7, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_colspan": 2,
         "col_dd_id": "USR_ID", "col_label": None, "col_target": "USR_ID",
         "col_visible": "Y", "col_disabled": "N", "col_required": "Y", "col_default": None},
        {"frm_id": 7, "col_id": 2, "tab_id": 1, "col_seq": 2, "col_colspan": None,
         "col_dd_id": "ROL_ID", "col_label": "Role", "col_target": "USR_ROLE_ID",
         "col_visible": "N", "col_disabled": "Y", "col_required": "N", "col_default": "ADMIN"},
        # placeholder (no col_target) — dropped
        {"frm_id": 7, "col_id": 3, "tab_id": 1, "col_seq": 3, "col_colspan": None,
         "col_dd_id": None, "col_label": None, "col_target": "",
         "col_visible": "Y", "col_disabled": "N", "col_required": "N", "col_default": None},
    ]
    filter_rows = [
        # VALUE literal — `{param=, value=}` ParamBind
        {"frm_id": 7, "col_id": 1, "flt_id": 1, "flt_type": "VALUE",
         "flt_source": None, "flt_target": "STATUS", "flt_value": "A"},
        # DD column-bind — `{param=, source=}` ParamBind
        {"frm_id": 7, "col_id": 2, "flt_id": 1, "flt_type": "DD",
         "flt_source": "USR_APPS_ID", "flt_target": "ROL_APPS_ID", "flt_value": None},
        # missing flt_target → dropped
        {"frm_id": 7, "col_id": 2, "flt_id": 2, "flt_type": "VALUE",
         "flt_source": None, "flt_target": None, "flt_value": "ignored"},
        # VALUE with blank flt_value → dropped
        {"frm_id": 7, "col_id": 2, "flt_id": 3, "flt_type": "VALUE",
         "flt_source": None, "flt_target": "X", "flt_value": ""},
    ]
    out = migrate_screens(
        table_rows, dialog_rows=[{"dlg_id": 1, "dlg_label": "Users"}],
        frm_rows=frm_rows, tab_rows=tab_rows, tab_l_rows=tab_l_rows,
        col_rows=col_rows, filter_rows=filter_rows, sql_rows=_SCR_SQL, app_name="nomasx1",
    )
    s = out["screens"]["nomasx1"]["security_users"]
    assert s["uploadable"] is True
    assert "audit" not in s  # tbl_audit None → key omitted
    assert "auto_load" not in s
    dialog = s["dialog"]
    assert [t["id"] for t in dialog["tabs"]] == ["general", "audit"]
    g = dialog["tabs"][0]
    assert g["label"] == "General" and g["cols"] == 2 and g["l"] == {"fr": "Général"}
    assert "hide_on_add" not in g and "hide_on_edit" not in g
    a = dialog["tabs"][1]
    assert a["hide_on_add"] is True and a["hide_on_edit"] is True and "cols" not in a
    fields = g["fields"]
    # Placeholder row (col_target='') is dropped.
    assert [f["name"] for f in fields] == ["USR_ID", "USR_ROLE_ID"]
    # First field: col_dd_id == col_target → `dd` key omitted; required=True; colspan=2.
    assert fields[0] == {
        "name": "USR_ID", "required": True, "colspan": 2,
        "lookup_param_binds": [{"param": "STATUS", "value": "A"}],
    }
    # Second field: dd override, label, hidden, disabled, default; one DD bind, the malformed ones dropped.
    assert fields[1] == {
        "name": "USR_ROLE_ID", "dd": "ROL_ID", "label": "Role",
        "hidden": True, "disabled": True, "default": "ADMIN",
        "lookup_param_binds": [{"param": "ROL_APPS_ID", "source": "USR_APPS_ID"}],
    }
    parse_screens(out)


def test_migrate_screens_with_nested_tabs() -> None:
    """v1 marked a tab as nested by putting one ly_dlg_col on it whose ``col_component`` was
    ``FormsDialog`` (child editable form) or ``FormsTable`` (child related-rows table). v2's
    migrator turns those into ``nested_form`` / ``nested_table`` ScreenTab variants — the
    operator complaint (empty JD Edwards / Activity Log tabs in security_applications) came
    from these tabs being dropped silently before this slice landed.

    The fixture mirrors v1's NOMASX1 SETTINGS_APPLICATIONS dialog shape: tab 1 a plain field
    grid (the parent's own columns), tab 2 a FormsDialog → nested form, tab 3 a FormsTable →
    nested table. Param-binds (ly_dlg_filters on the host dlg_col) carry parent → nested column
    mapping; v2 reuses the same ParamBind dicts."""
    # The "Activity Log" nested-table target — a sibling ly_tables row so :func:`migrate_screens`
    # assigns it a slug we can reference. (settings_applications + settings_activity_log share
    # the dataset; the nested_table tab points at the latter by id.)
    table_rows = [
        {"tbl_id": 31, "tbl_db_name": "settings_applications", "tbl_query_id": 31,
         "tbl_label": "Applications", "tbl_editable": "Y", "tbl_uploadable": "N",
         "tbl_audit": None, "tbl_auto_load": "Y", "tbl_frm_id": 1},
        {"tbl_id": 84, "tbl_db_name": "settings_activity_log", "tbl_query_id": 107,
         "tbl_label": "Activity Log", "tbl_editable": "N", "tbl_uploadable": "N",
         "tbl_audit": None, "tbl_auto_load": "Y", "tbl_frm_id": None},
    ]
    frm_rows = [
        {"frm_id": 1, "dlg_id": 1, "frm_query_id": 31, "frm_label": "Application"},
        # Nested editable form (jdedwards on the same APPS_ID PK).
        {"frm_id": 8, "dlg_id": 1, "frm_query_id": 97, "frm_label": "JD Edwards"},
    ]
    tab_rows = [
        {"frm_id": 1, "tab_id": 1, "tab_seq": 1, "tab_label": "General", "tab_cols": 1,
         "tab_disable_add": "N", "tab_disable_edit": "N"},
        {"frm_id": 1, "tab_id": 2, "tab_seq": 2, "tab_label": "JD Edwards", "tab_cols": 1,
         "tab_disable_add": "N", "tab_disable_edit": "N"},
        {"frm_id": 1, "tab_id": 3, "tab_seq": 3, "tab_label": "Activity Log", "tab_cols": 1,
         "tab_disable_add": "Y", "tab_disable_edit": "N"},
        # The nested form's own (single) tab — its fields live here.
        {"frm_id": 8, "tab_id": 1, "tab_seq": 1, "tab_label": "JD Edwards", "tab_cols": 1,
         "tab_disable_add": "N", "tab_disable_edit": "N"},
    ]
    col_rows = [
        # Parent tab 1 — a plain field grid.
        {"frm_id": 1, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_target": "APPS_ID",
         "col_visible": "Y", "col_disabled": "Y", "col_required": "Y",
         "col_component": "input", "col_component_id": None},
        # Parent tab 2 — host for the nested FormsDialog (target frm 8).
        {"frm_id": 1, "col_id": 14, "tab_id": 2, "col_seq": 1, "col_target": None,
         "col_visible": "Y", "col_disabled": "N", "col_required": "N",
         "col_component": "FormsDialog", "col_component_id": 8},
        # Parent tab 3 — host for the nested FormsTable (target tbl 84).
        {"frm_id": 1, "col_id": 16, "tab_id": 3, "col_seq": 1, "col_target": None,
         "col_visible": "Y", "col_disabled": "N", "col_required": "N",
         "col_component": "FormsTable", "col_component_id": 84},
        # Nested form's own fields (frm 8).
        {"frm_id": 8, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_target": "JDE_SY",
         "col_visible": "Y", "col_disabled": "N", "col_required": "N",
         "col_component": "input", "col_component_id": None},
        {"frm_id": 8, "col_id": 2, "tab_id": 1, "col_seq": 2, "col_target": "JDE_DTA",
         "col_visible": "Y", "col_disabled": "N", "col_required": "N",
         "col_component": "input", "col_component_id": None},
    ]
    filter_rows = [
        # Nested FormsDialog: bind parent.APPS_ID → nested .APPS_ID.
        {"frm_id": 1, "col_id": 14, "flt_id": 1, "flt_type": "DD",
         "flt_source": "APPS_ID", "flt_target": "APPS_ID", "flt_value": None},
        # Nested FormsTable: bind parent.APPS_ID → ACL_APPS_ID on activity_log.
        {"frm_id": 1, "col_id": 16, "flt_id": 1, "flt_type": "DD",
         "flt_source": "APPS_ID", "flt_target": "ACL_APPS_ID", "flt_value": None},
    ]
    # Stand-in sql_rows so :func:`migrate_screens` resolves the CRUD query names:
    #   - q31 → settings_applications_{get,put} (parent + writable)
    #   - q97 → settings_jdedwards_{get,put,post} (nested form CRUD)
    #   - q107 → settings_activity_log_get (read-only nested table)
    sql_rows = [
        {"query_id": 31, "query_crud": "GET", "query_label": "settings_applications", "query_pool": "nomasx1"},
        {"query_id": 31, "query_crud": "PUT", "query_label": "settings_applications", "query_pool": "nomasx1"},
        {"query_id": 97, "query_crud": "GET", "query_label": "settings_jdedwards", "query_pool": "nomasx1"},
        {"query_id": 97, "query_crud": "PUT", "query_label": "settings_jdedwards", "query_pool": "nomasx1"},
        {"query_id": 97, "query_crud": "POST", "query_label": "settings_jdedwards", "query_pool": "nomasx1"},
        {"query_id": 107, "query_crud": "GET", "query_label": "settings_activity_log", "query_pool": "nomasx1"},
    ]
    out = migrate_screens(
        table_rows, dialog_rows=[{"dlg_id": 1, "dlg_label": "Application"}],
        frm_rows=frm_rows, tab_rows=tab_rows, tab_l_rows=[],
        col_rows=col_rows, filter_rows=filter_rows, sql_rows=sql_rows, app_name="nomasx1",
    )
    s = out["screens"]["nomasx1"]["settings_applications"]
    tabs = s["dialog"]["tabs"]
    # Tab 0 — plain form tab; its single APPS_ID field rides on `fields`. `type` is implicit
    # ("form") so the emit drops it — the discriminated union's default lands at parse time.
    assert "type" not in tabs[0]  # implicit form
    assert tabs[0]["fields"][0]["name"] == "APPS_ID"
    # Tab 1 — nested form. read/update/insert resolved; binds carried; nested fields inline.
    nf = tabs[1]
    assert nf == {
        "id": "jd_edwards", "label": "JD Edwards",
        "type": "nested_form",
        "read_query": "settings_jdedwards_get",
        "update_query": "settings_jdedwards_put",
        "insert_query": "settings_jdedwards_post",
        "cols": 1,
        "fields": [
            {"name": "JDE_SY"},
            {"name": "JDE_DTA"},
        ],
        "param_binds": [{"param": "APPS_ID", "source": "APPS_ID"}],
    }
    # Tab 2 — nested table. `screen` references settings_activity_log (the sibling row);
    # the parent dlg_col's bind (APPS_ID → ACL_APPS_ID) carries through.
    nt = tabs[2]
    assert nt == {
        "id": "activity_log", "label": "Activity Log",
        "type": "nested_table",
        "screen": "settings_activity_log",
        "hide_on_add": True,
        "param_binds": [{"param": "ACL_APPS_ID", "source": "APPS_ID"}],
    }
    # Round-trips through the schema (discriminated union resolves each variant).
    parse_screens(out)


def test_migrate_screens_cross_connector() -> None:
    """Screen's read query lives on a *different* pool than the app — `connector` is set."""
    sql = [
        {"query_id": 20, "query_label": "F0005 List", "query_crud": "SELECT", "query_pool": "jdedwards",
         "query_dbtype": "oracle", "query_sqlquery": "SELECT * FROM f0005", "query_orderby": None},
    ]
    out = migrate_screens(
        [{"tbl_id": 1, "tbl_db_name": "f0005", "tbl_query_id": 20, "tbl_label": "F0005"}],
        sql_rows=sql, app_name="nomajde",
    )
    s = out["screens"]["nomajde"]["f0005"]
    assert s["connector"] == "jdedwards"
    # Same-pool: `connector` is omitted.
    out2 = migrate_screens(
        [{"tbl_id": 1, "tbl_db_name": "f0005", "tbl_query_id": 20, "tbl_label": "F0005"}],
        sql_rows=sql, app_name="jdedwards",
    )
    assert "connector" not in out2["screens"]["jdedwards"]["f0005"]


def test_migrate_screens_crud_normalization() -> None:
    """The CRUD map normalises v1's REST verbs + SQL keywords to GET/PUT/POST/DELETE for
    *routing* (which slot — read_query / update_query / insert_query / delete_query — the
    name lands in), but the v2 *name* keeps the raw v1 crud verbatim so it matches what
    :func:`migrate_sql_queries` emits in ``connectors.toml`` for that pool."""
    # Four CRUD families, each spelled in different v1 styles. First-row-wins per family.
    sql = [
        # GET family — REST verb wins (would match v1 REST data)
        {"query_id": 30, "query_label": "Items", "query_crud": "SELECT", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "SELECT 1", "query_orderby": None},
        # UPDATE → PUT slot
        {"query_id": 30, "query_label": "Items", "query_crud": "UPDATE", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "UPDATE", "query_orderby": None},
        # MERGE / INSERT both map to POST slot — first row wins
        {"query_id": 30, "query_label": "Items", "query_crud": "MERGE", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "MERGE", "query_orderby": None},
        {"query_id": 30, "query_label": "Items", "query_crud": "INSERT", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "INSERT", "query_orderby": None},
        # PATCH → PUT slot (loses to UPDATE — first wins)
        {"query_id": 30, "query_label": "Items", "query_crud": "PATCH", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "UPDATE", "query_orderby": None},
        # REMOVE → DELETE slot
        {"query_id": 30, "query_label": "Items", "query_crud": "REMOVE", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "DELETE", "query_orderby": None},
    ]
    out = migrate_screens(
        [{"tbl_id": 1, "tbl_db_name": "items", "tbl_query_id": 30, "tbl_label": "Items"}],
        sql_rows=sql, app_name="nomasx1",
    )
    s = out["screens"]["nomasx1"]["items"]
    # Names mirror migrate_sql_queries' convention: slugify(<label>_<RAW v1 crud>).
    assert s["read_query"] == "items_select"     # SELECT row landed in the GET slot
    assert s["update_query"] == "items_update"   # UPDATE row landed in the PUT slot
    assert s["insert_query"] == "items_merge"    # MERGE first → POST slot (INSERT/PATCH dropped)
    assert s["delete_query"] == "items_remove"   # REMOVE row landed in the DELETE slot


def test_migrate_screens_skips_unreadable() -> None:
    """A table whose query has no GET/SELECT companion → skipped (would be broken at runtime)."""
    sql = [
        {"query_id": 99, "query_label": "Only Delete", "query_crud": "DELETE", "query_pool": "p",
         "query_dbtype": "postgres", "query_sqlquery": "DELETE", "query_orderby": None},
    ]
    out = migrate_screens(
        [{"tbl_id": 1, "tbl_db_name": "x", "tbl_query_id": 99, "tbl_label": "X"}],
        sql_rows=sql, app_name="p",
    )
    assert out == {"screens": {"p": {}}}


def test_migrate_screens_id_dedup_and_fallbacks() -> None:
    """Two tables that slug to the same id → ``_2`` suffix; rows missing both
    ``tbl_db_name`` and ``tbl_label`` fall back to ``screen_<tbl_id>``."""
    rows = [
        # both yield "users"
        {"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users"},
        {"tbl_id": 2, "tbl_db_name": None, "tbl_query_id": 10, "tbl_label": "Users"},
        # no name at all → fallback to screen_3
        {"tbl_id": 3, "tbl_db_name": "", "tbl_query_id": 10, "tbl_label": ""},
    ]
    out = migrate_screens(rows, sql_rows=_SCR_SQL, app_name="nomasx1")
    assert list(out["screens"]["nomasx1"]) == ["users", "users_2", "screen_3"]


def test_migrate_screens_no_dialog_when_frm_unresolved() -> None:
    """``tbl_frm_id`` pointing at a non-existent form (or one with no tabs) → no dialog."""
    rows = [{"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users", "tbl_frm_id": 999}]
    out = migrate_screens(rows, sql_rows=_SCR_SQL, app_name="nomasx1")
    assert "dialog" not in out["screens"]["nomasx1"]["users"]


def test_migrate_screens_visible_when_from_cdn() -> None:
    """A field with ``col_cdn_id`` picks up its conditional-visibility predicates from
    ``ly_cdn_params`` — same shape as :func:`migrate_column_visibility`, but the dd→target
    resolver runs *per frm* (a predicate's `cdn_dd_id` resolves to another field on the same
    dialog). Unsupported operators (NOT_EQUAL / LIKE / …) leave the field unconstrained
    (always-visible) — never accidentally hidden."""
    table_rows = [{"tbl_id": 1, "tbl_db_name": "items", "tbl_query_id": 10, "tbl_label": "Items", "tbl_frm_id": 7}]
    frm_rows = [{"frm_id": 7, "dlg_id": 1, "frm_query_id": 10, "frm_label": "Item"}]
    tab_rows = [{"frm_id": 7, "tab_id": 1, "tab_seq": 1, "tab_label": "General", "tab_cols": None, "tab_disable_add": "N", "tab_disable_edit": "N"}]
    col_rows = [
        # Predicate source — a "type" picker that gates two other fields. Its own dd is `KIND`.
        {"frm_id": 7, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_dd_id": "KIND", "col_target": "ITM_KIND", "col_visible": "Y"},
        # Conditional field 1 — col_cdn_id 100; predicate KIND=PRODUCT keeps it visible.
        {"frm_id": 7, "col_id": 2, "tab_id": 1, "col_seq": 2, "col_dd_id": "SKU", "col_target": "ITM_SKU",
         "col_visible": "Y", "col_cdn_id": 100},
        # Conditional field 2 — col_cdn_id 101; AND-ed predicates (KIND in [PRODUCT, SERVICE] AND TIER=PRO).
        {"frm_id": 7, "col_id": 3, "tab_id": 1, "col_seq": 3, "col_dd_id": "PRICE", "col_target": "ITM_PRICE",
         "col_visible": "Y", "col_cdn_id": 101},
        # Field with cdn pointing at an unsupported operator → should be left unconstrained.
        {"frm_id": 7, "col_id": 4, "tab_id": 1, "col_seq": 4, "col_dd_id": "SLA", "col_target": "ITM_SLA",
         "col_visible": "Y", "col_cdn_id": 102},
        # Helper field referenced by the cdn (KIND→ITM_KIND already; TIER→ITM_TIER here).
        {"frm_id": 7, "col_id": 5, "tab_id": 1, "col_seq": 5, "col_dd_id": "TIER", "col_target": "ITM_TIER", "col_visible": "Y"},
    ]
    cdn_params = [
        {"cdn_id": 100, "cdn_seq": 1, "cdn_dd_id": "KIND", "cdn_operator": "EQUAL", "cdn_value": "PRODUCT"},
        {"cdn_id": 101, "cdn_seq": 1, "cdn_dd_id": "KIND", "cdn_operator": "EQUAL", "cdn_value": "PRODUCT"},
        {"cdn_id": 101, "cdn_seq": 2, "cdn_dd_id": "KIND", "cdn_operator": "EQUAL", "cdn_value": "SERVICE"},
        {"cdn_id": 101, "cdn_seq": 3, "cdn_dd_id": "TIER", "cdn_operator": "EQUAL", "cdn_value": "PRO"},
        {"cdn_id": 102, "cdn_seq": 1, "cdn_dd_id": "KIND", "cdn_operator": "NOT_EQUAL", "cdn_value": "X"},  # unsupported
    ]
    out = migrate_screens(
        table_rows, frm_rows=frm_rows, tab_rows=tab_rows, col_rows=col_rows,
        cdn_param_rows=cdn_params, sql_rows=_SCR_SQL, app_name="nomasx1",
    )
    fields = {f["name"]: f for f in out["screens"]["nomasx1"]["items"]["dialog"]["tabs"][0]["fields"]}
    # KIND has no cdn → no visible_when key emitted
    assert "visible_when" not in fields["ITM_KIND"]
    # cdn 100: one predicate, one allowed value → list with one rule, value sorted
    assert fields["ITM_SKU"]["visible_when"] == [{"field": "ITM_KIND", "value": ["PRODUCT"]}]
    # cdn 101: two AND-ed predicates — KIND in {PRODUCT, SERVICE} (sorted) and TIER == PRO,
    # field names resolved to the on-frm col_targets.
    assert fields["ITM_PRICE"]["visible_when"] == [
        {"field": "ITM_KIND", "value": ["PRODUCT", "SERVICE"]},
        {"field": "ITM_TIER", "value": ["PRO"]},
    ]
    # cdn 102: unsupported operator → left unconstrained (no visible_when emitted)
    assert "visible_when" not in fields["ITM_SLA"]
    # Round-trip through the screens schema (FieldCondition validates).
    parse_screens(out)


def test_migrate_actions_dumps_v1_workflows() -> None:
    """v1's named-action workflows (``ly_actions`` + ``ly_act_tasks`` + ``ly_act_branch`` + the
    params + per-task-param tables) get **dumped** into a documented ``[migrated_actions.<app>]``
    block — v2's flat ``Action`` union can't model the branch graph, so the migrator captures
    every v1 field faithfully and a human re-implements via the screen builder. QUERY tasks
    resolve to v2 query names via ``ly_qry_sql`` (matching what ``migrate_sql_queries`` emits);
    unresolvable ones keep a ``warning`` so the operator notices."""
    # Mirrors libnjde's "Import Security" action (act_id 3) shape: an IF with branches, a
    # QUERY referencing query 23 with crud DELETE, then a QUERY for crud POST.
    action_rows = [
        {"act_id": 3, "act_label": "Import Security from User / Role"},
    ]
    task_rows = [
        {"act_id": 3, "evt_id": 2, "evt_seq": 1, "evt_type": "IF",
         "evt_label": "Check if import security workbench",
         "evt_query_id": None, "evt_query_crud": None, "evt_api_id": None,
         "evt_brc_id": None, "evt_brc_true": 1, "evt_brc_false": None,
         "evt_loop": "N", "evt_loop_array": None},
        {"act_id": 3, "evt_id": 3, "evt_seq": 2, "evt_type": "QUERY",
         "evt_label": "Delete Security Workbench",
         "evt_query_id": 23, "evt_query_crud": "DELETE", "evt_api_id": None,
         "evt_brc_id": 1, "evt_brc_true": None, "evt_brc_false": None,
         "evt_loop": "N", "evt_loop_array": None},
        {"act_id": 3, "evt_id": 1, "evt_seq": 3, "evt_type": "QUERY",
         "evt_label": "Import Security Workbench",
         "evt_query_id": 23, "evt_query_crud": "POST", "evt_api_id": None,
         "evt_brc_id": 1, "evt_brc_true": None, "evt_brc_false": None,
         "evt_loop": "N", "evt_loop_array": None},
    ]
    branch_rows = [
        {"act_id": 3, "brc_id": 1, "brc_label": "Import Security Workbench"},
    ]
    param_rows = [
        {"act_id": 3, "map_var": "ROLE", "map_dir": "IN", "map_display": "Y",
         "map_rules": None, "map_rules_values": None, "map_default": None},
        {"act_id": 3, "map_var": "USER", "map_dir": "IN", "map_display": "Y",
         "map_rules": None, "map_rules_values": None, "map_default": None},
    ]
    task_param_rows = [
        # Reference to an action-level INPUT param (libnjde's convention: map_value = "INPUT.<var>")
        {"act_id": 3, "evt_id": 1, "map_id": 1, "map_type": "DD", "map_dir": "IN",
         "map_var": "FROMUSER", "map_label": None, "map_var_type": None,
         "map_value": "INPUT.ROLE", "map_rules": None, "map_rules_values": None, "map_default": None},
        # A literal VALUE
        {"act_id": 3, "evt_id": 1, "map_id": 2, "map_type": "VALUE", "map_dir": "IN",
         "map_var": "TOUSER", "map_label": None, "map_var_type": None,
         "map_value": "ADMIN", "map_rules": None, "map_rules_values": None, "map_default": None},
    ]
    param_filter_rows = []
    sql_rows = [
        {"query_id": 23, "query_crud": "DELETE", "query_label": "f00950", "query_pool": "jdedwards"},
        {"query_id": 23, "query_crud": "POST", "query_label": "f00950", "query_pool": "jdedwards"},
    ]
    out = migrate_actions(
        action_rows, task_rows, branch_rows, param_rows, task_param_rows, param_filter_rows,
        sql_rows=sql_rows, app_name="nomajde",
    )
    actions = out["migrated_actions"]["nomajde"]
    assert set(actions) == {"import_security_from_user_role"}
    a = actions["import_security_from_user_role"]
    assert a["label"] == "Import Security from User / Role"
    assert a["v1_act_id"] == 3
    # Action-level params carried through.
    assert [p["name"] for p in a["params"]] == ["ROLE", "USER"]
    # Branches surfaced for the operator.
    assert a["branches"] == [{"id": 1, "label": "Import Security Workbench"}]
    # Tasks in seq order; IF task has on_true_branch + no v2 query; QUERY tasks resolve to v2
    # names following the migrate_sql_queries naming convention (raw crud verbatim).
    tasks = a["tasks"]
    assert len(tasks) == 3
    assert tasks[0]["type"] == "IF" and tasks[0]["on_true_branch"] == 1 and "query" not in tasks[0]
    assert tasks[1]["type"] == "QUERY" and tasks[1]["query"] == "f00950_delete"
    assert tasks[1]["belongs_to_branch"] == 1
    assert tasks[2]["query"] == "f00950_post"
    # Per-task param binds — INPUT.X collapses to a `source` reference; literal VALUE → `value`.
    assert tasks[2]["param_binds"] == [
        {"param": "FROMUSER", "source": "ROLE"},
        {"param": "TOUSER", "value": "ADMIN"},
    ]


def test_attach_actions_to_screens_event_driven() -> None:
    """v1's ``ly_evt_cpt`` is the *correct* attachment for actions: each row says "event N on
    component C fires action A". ``FormsDialog`` rows wire to the matching v2 screen's
    ``dialog.on_save``; the action's tasks become a sequential ``run_query`` chain there. The
    **first task is skipped when its query matches the screen's main update/insert** — avoids
    duplicating the row write the dialog Save already performs."""
    screens_data = {"screens": {"nomajde": {
        "f0092": {
            "id": "f0092", "label": "Role Description",
            "read_query": "f0092_get", "update_query": "f0092_put",
            "dialog": {"tabs": [{"id": "general", "type": "form", "fields": []}]},
        },
    }}}
    actions_data = {"migrated_actions": {"nomajde": {
        "create_role": {
            "id": "create_role", "v1_act_id": 1, "label": "Create Role in all tables",
            "tasks": [
                # First task matches the screen's update_query → skipped (dialog already writes it).
                {"seq": 1, "v1_evt_id": 1, "type": "QUERY", "label": "Insert role",
                 "query": "f0092_put",
                 "param_binds": [{"param": "ULUSER", "source": "AUUSER"}]},
                # Subsequent tasks become the on_save chain.
                {"seq": 2, "v1_evt_id": 2, "type": "QUERY", "label": "Insert role/lang",
                 "query": "f00921_put"},
                {"seq": 3, "v1_evt_id": 3, "type": "QUERY", "label": "Insert role/env",
                 "query": "f0093_put"},
            ],
        },
    }}}
    # The event row + the table row that links frm 2 → tbl_id 11 (with read query for f0092).
    event_rows = [{"evt_component": "FormsDialog", "evt_cpt_id": 2, "evt_id": 1, "evt_act_id": 1}]
    table_rows = [
        {"tbl_id": 11, "tbl_db_name": "f0092", "tbl_label": "Role Description", "tbl_frm_id": 2},
    ]
    out = attach_actions_to_screens(
        screens_data, actions_data,
        event_rows=event_rows, table_rows=table_rows,
        app_name="nomajde",
    )
    on_save = out["screens"]["nomajde"]["f0092"]["dialog"]["on_save"]
    # First task (f0092_put) skipped because it duplicates update_query. Two run_query
    # entries remain, in seq order, each labelled from the v1 task.
    assert [a["query"] for a in on_save] == ["f00921_put", "f0093_put"]
    assert all(a["type"] == "run_query" for a in on_save)
    assert all(a["id"].startswith("migrated_1_") for a in on_save)
    # No ``Screen.actions`` block landed — events go on ``dialog.on_save``, not the toolbar.
    assert "actions" not in out["screens"]["nomajde"]["f0092"]


def test_attach_actions_to_screens_dedupes_table_and_dialog_events() -> None:
    """v1 frequently wires the same action to both a FormsDialog *and* its FormsTable (Save
    + row insert fire the same workflow). The attach function dedupes by ``(target_screen,
    v1_act_id)`` so the on_save chain doesn't double up."""
    screens_data = {"screens": {"nomajde": {
        "f0092": {
            "id": "f0092", "read_query": "f0092_get", "update_query": "f0092_put",
            "dialog": {"tabs": [{"id": "g", "type": "form", "fields": []}]},
        },
    }}}
    actions_data = {"migrated_actions": {"nomajde": {
        "create_role": {
            "id": "create_role", "v1_act_id": 1, "label": "Create Role",
            "tasks": [{"seq": 1, "v1_evt_id": 1, "type": "QUERY", "query": "f00921_put"}],
        },
    }}}
    event_rows = [
        {"evt_component": "FormsDialog", "evt_cpt_id": 2, "evt_id": 1, "evt_act_id": 1},
        {"evt_component": "FormsTable",  "evt_cpt_id": 11, "evt_id": 2, "evt_act_id": 1},
    ]
    table_rows = [{"tbl_id": 11, "tbl_db_name": "f0092", "tbl_label": "Role Description", "tbl_frm_id": 2}]
    out = attach_actions_to_screens(
        screens_data, actions_data,
        event_rows=event_rows, table_rows=table_rows,
        app_name="nomajde",
    )
    on_save = out["screens"]["nomajde"]["f0092"]["dialog"]["on_save"]
    # Despite two events pointing at action 1, the chain lands once.
    assert [a["query"] for a in on_save] == ["f00921_put"]


def test_attach_actions_to_screens_scrubs_prior_heuristic_entries() -> None:
    """The previous slice's keyword-based heuristic attached actions to ``Screen.actions``
    with ids starting ``migrated_<n>``. Those were misplaced. Re-running with the new event-
    driven model scrubs them — both from ``Screen.actions`` and from ``dialog.on_save``,
    whichever they ended up in. Hand-wired entries (without the ``migrated_`` prefix) stay."""
    screens_data = {"screens": {"nomajde": {
        "f0092": {
            "id": "f0092", "read_query": "f0092_get", "update_query": "f0092_put",
            # Hand-wired + prior-heuristic mix on Screen.actions.
            "actions": [
                {"id": "hand_wired", "type": "notify", "message": "hi", "tone": "info"},
                {"id": "migrated_1", "type": "run_query", "query": "stale_query"},
            ],
            "dialog": {
                "tabs": [{"id": "g", "type": "form", "fields": []}],
                # And a hand-wired entry mixed with a prior auto-attached one on dialog.on_save.
                "on_save": [
                    {"id": "user_added_notify", "type": "notify", "message": "post-save", "tone": "ok"},
                    {"id": "migrated_42_0", "type": "run_query", "query": "another_stale"},
                ],
            },
        },
    }}}
    # No events / no actions — but the scrub still runs.
    out = attach_actions_to_screens(
        screens_data, {"migrated_actions": {"nomajde": {}}},
        event_rows=[], table_rows=[],
        app_name="nomajde",
    )
    scr = out["screens"]["nomajde"]["f0092"]
    assert [a["id"] for a in scr["actions"]] == ["hand_wired"]
    assert [a["id"] for a in scr["dialog"]["on_save"]] == ["user_added_notify"]


def test_attach_actions_to_screens_carries_prompt_fields() -> None:
    """v1 ``ly_act_params`` (a workflow's input arguments) → v2 ``prompt_fields`` on the **first
    emitted task** of the chain. The runtime opens a sub-dialog before that task fires; the
    operator's input merges into the chain's resolution context so every subsequent task's
    ``ParamBind {source: '<NAME>'}`` reads from the prompt values.

    Output-only params (``map_dir = 'OUT'``) are skipped — SP returns, not user inputs.
    ``map_display = 'N'`` becomes ``hidden = true`` so the field still threads through but
    doesn't show. ``filters`` (ly_act_params_filters) → ``lookup_param_binds`` on the prompt
    field, so a LOOKUP-typed prompt field cascades correctly once the operator picks a dd."""
    screens_data = {"screens": {"nomajde": {
        "f0092": {
            "id": "f0092", "read_query": "f0092_get", "update_query": "f0092_put",
            "dialog": {"tabs": [{"id": "g", "type": "form", "fields": []}]},
        },
    }}}
    actions_data = {"migrated_actions": {"nomajde": {
        "create_role": {
            "id": "create_role", "v1_act_id": 1, "label": "Create Role",
            # The five inputs the v1 "Create Role" workflow declared. Two flavours mixed:
            # an IN with a default + filters (the LOOKUP-style picker); a hidden technical
            # param; an OUT (skipped); a plain INOUT.
            "params": [
                {"name": "AUUSER", "direction": "IN", "default": "ADMIN",
                 "filters": [{"param": "POOL", "value": "JDE"}]},
                {"name": "JOBN", "direction": "IN"},
                {"name": "MUSE", "direction": "IN", "display": "N"},  # hidden
                {"name": "PID", "direction": "INOUT"},
                {"name": "UPMJ", "direction": "OUT"},   # SP return → skipped
            ],
            "tasks": [
                # Skipped — duplicates update_query, the dialog Save already runs it.
                {"seq": 1, "type": "QUERY", "query": "f0092_put",
                 "param_binds": [{"param": "ULUSER", "source": "AUUSER"}]},
                # The follow-up — this is where prompt_fields land.
                {"seq": 2, "type": "QUERY", "query": "f00921_put",
                 "param_binds": [{"param": "MUSE_BIND", "source": "MUSE"}]},
                {"seq": 3, "type": "QUERY", "query": "f0093_put"},
            ],
        },
    }}}
    event_rows = [{"evt_component": "FormsDialog", "evt_cpt_id": 2, "evt_id": 1, "evt_act_id": 1}]
    table_rows = [{"tbl_id": 11, "tbl_db_name": "f0092", "tbl_label": "Role Description", "tbl_frm_id": 2}]
    out = attach_actions_to_screens(
        screens_data, actions_data,
        event_rows=event_rows, table_rows=table_rows,
        app_name="nomajde",
    )
    on_save = out["screens"]["nomajde"]["f0092"]["dialog"]["on_save"]
    assert [a["query"] for a in on_save] == ["f00921_put", "f0093_put"]
    # Prompt fields land on the FIRST emitted task only — fires once per chain. Subsequent
    # tasks resolve `source: 'MUSE'` against the merged context (the prompt values).
    assert "prompt_fields" in on_save[0]
    assert "prompt_fields" not in on_save[1]
    names = [pf["name"] for pf in on_save[0]["prompt_fields"]]
    assert names == ["AUUSER", "JOBN", "MUSE", "PID"]   # OUT 'UPMJ' skipped
    pf_by_name = {pf["name"]: pf for pf in on_save[0]["prompt_fields"]}
    assert pf_by_name["AUUSER"]["default"] == "ADMIN"
    assert pf_by_name["AUUSER"]["lookup_param_binds"] == [{"param": "POOL", "value": "JDE"}]
    assert pf_by_name["MUSE"].get("hidden") is True
    # JOBN has no extras — just `name` is enough.
    assert pf_by_name["JOBN"] == {"name": "JOBN"}


def test_migrate_actions_unresolvable_query_keeps_warning() -> None:
    """A task whose ``evt_query_id`` doesn't appear in ``ly_qry_sql`` (or whose crud isn't a
    SCREEN_CRUD_MAP value) keeps the v1 ids + a ``warning`` field so the operator can re-route
    it. v1 schemas occasionally reference queries that were renamed / dropped — failing loudly
    here is better than silently dropping a task."""
    out = migrate_actions(
        [{"act_id": 1, "act_label": "Orphan"}],
        [{"act_id": 1, "evt_id": 1, "evt_seq": 1, "evt_type": "QUERY", "evt_label": "ghost",
          "evt_query_id": 999, "evt_query_crud": "GET", "evt_api_id": None,
          "evt_brc_id": None, "evt_brc_true": None, "evt_brc_false": None,
          "evt_loop": "N", "evt_loop_array": None}],
        sql_rows=[],  # 999 doesn't exist
        app_name="nomajde",
    )
    task = out["migrated_actions"]["nomajde"]["orphan"]["tasks"][0]
    assert task["v1_query_id"] == 999 and task["v1_query_crud"] == "GET"
    assert "warning" in task and "no matching v2 query" in task["warning"]


def test_migrate_context_menus_basic() -> None:
    """v1's row context menus (``ly_ctxmenus`` + ``ly_ctx_val`` + ``ly_ctx_filters``) collapse
    to a list of v2 ``NavigateAction``s per referencing screen — ``FormsTable`` items target a
    table's read query, ``FormsDialog`` items target the screen behind a dialog form. ParamBinds
    on each item land verbatim. Cross-pool targets carry an explicit ``connector``; same-pool
    targets leave it implicit. An item whose target can't be resolved is skipped with a warning."""
    ctx_rows = [
        {"ctx_id": 1, "ctx_description": "Security - Users"},
        {"ctx_id": 2, "ctx_description": "Cross-pool example"},
    ]
    val_rows = [
        # ctx 1 — local FormsTable + FormsDialog drills
        {"ctx_id": 1, "val_id": 1, "val_seq": 1, "val_label": "Display Roles",
         "val_component": "FormsTable", "val_component_id": 100},
        {"ctx_id": 1, "val_id": 2, "val_seq": 2, "val_label": "Display Properties",
         "val_component": "FormsDialog", "val_component_id": 7},
        # ctx 1 — points at a tbl_id that has no entry → skipped
        {"ctx_id": 1, "val_id": 3, "val_seq": 3, "val_label": "Orphan",
         "val_component": "FormsTable", "val_component_id": 999},
        # ctx 2 — drill to a target on a different pool → emits `connector`
        {"ctx_id": 2, "val_id": 1, "val_seq": 1, "val_label": "Drill JDE",
         "val_component": "FormsTable", "val_component_id": 200},
    ]
    filter_rows = [
        # ctx 1 / val 1 — two DD binds (column sources from the source row)
        {"ctx_id": 1, "val_id": 1, "flt_id": 1, "flt_type": "DD",
         "flt_source": "USR_APPS_ID", "flt_target": "RLU_APPS_ID", "flt_value": None},
        {"ctx_id": 1, "val_id": 1, "flt_id": 2, "flt_type": "DD",
         "flt_source": "USR_ID", "flt_target": "RLU_USER_ID", "flt_value": None},
        # ctx 1 / val 2 — one VALUE literal
        {"ctx_id": 1, "val_id": 2, "flt_id": 1, "flt_type": "VALUE",
         "flt_source": None, "flt_target": "STATUS", "flt_value": "A"},
        # ctx 1 / val 2 — missing flt_target → dropped
        {"ctx_id": 1, "val_id": 2, "flt_id": 2, "flt_type": "VALUE",
         "flt_source": None, "flt_target": None, "flt_value": "ignored"},
    ]
    tables_rows = [
        # screen 5 references ctx 1 (Security - Users) — inline copy lands here on save
        {"tbl_id": 5, "tbl_db_name": "security_users", "tbl_query_id": 10, "tbl_ctx_id": 1},
        # the target table for ctx 1 / val 1 (FormsTable, val_component_id = 100) — query 20
        {"tbl_id": 100, "tbl_db_name": "security_assignments", "tbl_query_id": 20},
        # screen 6 references ctx 2 (cross-pool drill)
        {"tbl_id": 6, "tbl_db_name": "settings", "tbl_query_id": 30, "tbl_ctx_id": 2},
        # the target table for ctx 2 / val 1 — query 40 (lives on the `jdedwards` pool)
        {"tbl_id": 200, "tbl_db_name": "f0005", "tbl_query_id": 40},
        # screen 7 — no tbl_ctx_id → no row_menu in the result
        {"tbl_id": 7, "tbl_db_name": "rights", "tbl_query_id": 50},
    ]
    dlg_frm_rows = [
        # ctx 1 / val 2 (FormsDialog, val_component_id = 7) — frm 7 points at query 21
        {"frm_id": 7, "frm_query_id": 21},
    ]
    sql_rows = [
        {"query_id": 10, "query_label": "Users", "query_crud": "GET", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "SELECT * FROM users", "query_orderby": None},
        {"query_id": 20, "query_label": "Assignments", "query_crud": "GET", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "SELECT * FROM assignments", "query_orderby": None},
        {"query_id": 21, "query_label": "Users Props", "query_crud": "GET", "query_pool": "nomasx1",
         "query_dbtype": "postgres", "query_sqlquery": "SELECT * FROM usr_props", "query_orderby": None},
        # query 40 lives on a different pool — `connector` will be emitted on the action
        {"query_id": 40, "query_label": "F0005", "query_crud": "GET", "query_pool": "jdedwards",
         "query_dbtype": "oracle", "query_sqlquery": "SELECT * FROM f0005", "query_orderby": None},
    ]
    out, promotable = migrate_context_menus(
        ctx_rows, val_rows, filter_rows,
        tables_rows=tables_rows, dlg_frm_rows=dlg_frm_rows, sql_rows=sql_rows,
        app_name="nomasx1",
    )
    # Two screens have a tbl_ctx_id → both get an inline row_menu copy. The orphan val_id 3 on
    # ctx 1 is skipped (val_component_id 999 doesn't resolve to any ly_tables row).
    assert set(out) == {5, 6}
    # Promotable candidates — sec_users' menu has exactly one FormsDialog item (Display
    # Properties), so it's a candidate for row_click promotion. Drill (tbl_id 6) has only a
    # FormsTable item, so nothing to promote there.
    assert set(promotable) == {5}
    assert promotable[5]["action_id"] == "display_properties"
    sec_users = out[5]
    assert [a["id"] for a in sec_users] == ["display_roles", "display_properties"]
    # First item: FormsTable → screen 100 → query 20 → `assignments_get`; binds round-trip.
    assert sec_users[0] == {
        "id": "display_roles",
        "type": "navigate",
        "to": "assignments_get",
        "label": "Display Roles",
        "param_binds": [
            {"param": "RLU_APPS_ID", "source": "USR_APPS_ID"},
            {"param": "RLU_USER_ID", "source": "USR_ID"},
        ],
    }
    # Second item: FormsDialog → frm 7 → query 21 → `users_props_get`; VALUE bind round-trips.
    assert sec_users[1] == {
        "id": "display_properties",
        "type": "navigate",
        "to": "users_props_get",
        "label": "Display Properties",
        "param_binds": [{"param": "STATUS", "value": "A"}],
    }
    # Screen 6 — cross-pool drill: target lives on `jdedwards`, the app is `nomasx1`, so
    # `connector` is emitted explicitly. (Same-pool targets leave it implicit.)
    drill = out[6]
    assert drill == [
        {"id": "drill_jde", "type": "navigate", "to": "f0005_get",
         "connector": "jdedwards", "label": "Drill JDE"},
    ]


def test_migrate_screens_with_row_menus() -> None:
    """`migrate_screens` accepts a pre-computed ``row_menus`` map and inlines the matching
    actions onto each screen's ``row_menu`` field, keyed by ``tbl_id``."""
    table_rows = [
        {"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users"},
        {"tbl_id": 2, "tbl_db_name": "no_menu", "tbl_query_id": 10, "tbl_label": "Plain"},
    ]
    row_menus = {
        1: [
            {"id": "act_a", "type": "navigate", "to": "things_get", "label": "Things"},
        ],
        # 2: nothing — the screen for tbl_id 2 ends up without row_menu, validating the per-tbl_id keying
    }
    out = migrate_screens(table_rows, sql_rows=_SCR_SQL, row_menus=row_menus, app_name="nomasx1")
    screens = out["screens"]["nomasx1"]
    assert screens["users"]["row_menu"] == [
        {"id": "act_a", "type": "navigate", "to": "things_get", "label": "Things"},
    ]
    assert "row_menu" not in screens["no_menu"]
    # Round-trips through the screens schema (NavigateAction validates).
    parse_screens(out)


def test_migrate_screens_promotes_single_forms_dialog_ctx_menu_item() -> None:
    """v1's NOMASX1 ``SECURITY_USERS`` table has no ``tbl_frm_id`` of its own; instead its ctx
    menu carried a single ``FormsDialog`` item ("Display Properties") that pointed at a sibling
    table's form. The migrator promotes that item to ``Screen.row_click_screen`` so the row click
    opens the target screen's dialog as a modal — and drops the now-redundant menu entry. Other
    items (FormsTable drills) survive untouched."""
    table_rows = [
        # Parent screen: read query 10 → ``users_get``. No tbl_frm_id → no own dialog.
        {"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users",
         "tbl_editable": "Y", "tbl_uploadable": "N", "tbl_audit": None, "tbl_auto_load": "Y",
         "tbl_frm_id": None},
        # Target screen — owns the properties dialog (frm 2 → query 20 → users_props_get).
        # Has tbl_frm_id=2 so the migrator builds the dialog from frm 2.
        {"tbl_id": 38, "tbl_db_name": "users_props", "tbl_query_id": 20, "tbl_label": "Properties",
         "tbl_editable": "Y", "tbl_uploadable": "N", "tbl_audit": None, "tbl_auto_load": "Y",
         "tbl_frm_id": 2},
    ]
    frm_rows = [{"frm_id": 2, "dlg_id": 1, "frm_query_id": 20, "frm_label": "Properties"}]
    tab_rows = [
        {"frm_id": 2, "tab_id": 1, "tab_seq": 1, "tab_label": "General", "tab_cols": 2,
         "tab_disable_add": "N", "tab_disable_edit": "N"},
    ]
    col_rows = [
        {"frm_id": 2, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_target": "USRP_ID",
         "col_visible": "Y", "col_disabled": "Y", "col_required": "Y"},
        {"frm_id": 2, "col_id": 2, "tab_id": 1, "col_seq": 2, "col_target": "USRP_NAME",
         "col_visible": "Y", "col_disabled": "N", "col_required": "Y"},
    ]
    # sql_rows: query 10 is the parent read; query 20 has GET+PUT (so the target screen has a
    # writable dialog — promotion requires both ``dialog`` and ``update_query`` on the target).
    sql_rows = [
        {"query_id": 10, "query_crud": "GET", "query_label": "users", "query_pool": "nomasx1"},
        {"query_id": 20, "query_crud": "GET", "query_label": "users_props", "query_pool": "nomasx1"},
        {"query_id": 20, "query_crud": "PUT", "query_label": "users_props", "query_pool": "nomasx1"},
    ]
    row_menus = {
        1: [
            # The promotable: FormsDialog (its v1 val_label was "Display Properties"). After
            # promotion, this entry is dropped from the row menu.
            {"id": "display_properties", "type": "navigate", "to": "users_props_get",
             "label": "Display Properties",
             "param_binds": [
                 {"param": "USRP_ID", "source": "USR_ID"},
                 {"param": "USRP_APPS_ID", "source": "USR_APPS_ID"},
             ]},
            # A normal FormsTable drill — survives.
            {"id": "display_roles", "type": "navigate", "to": "roles_get", "label": "Display Roles"},
        ],
    }
    # Promotable map — what migrate_context_menus would have returned for tbl_id=1.
    promotable = {
        1: {
            "action_id": "display_properties",
            "target_qid": 20,
            "binds": [
                {"param": "USRP_ID", "source": "USR_ID"},
                {"param": "USRP_APPS_ID", "source": "USR_APPS_ID"},
            ],
            "target_pool": "nomasx1",
        },
    }
    out = migrate_screens(
        table_rows, frm_rows=frm_rows, tab_rows=tab_rows, col_rows=col_rows,
        sql_rows=sql_rows, row_menus=row_menus, promotable_dialogs=promotable,
        app_name="nomasx1",
    )
    screens = out["screens"]["nomasx1"]
    users = screens["users"]
    # Promotion landed: row_click_screen points at the sibling, binds carried, connector implicit
    # (same as parent's effective). The redundant "Display Properties" menu item is gone; the
    # FormsTable drill survives.
    assert users["row_click_screen"] == "users_props"
    assert "row_click_connector" not in users  # same connector as parent
    assert users["row_click_binds"] == [
        {"param": "USRP_ID", "source": "USR_ID"},
        {"param": "USRP_APPS_ID", "source": "USR_APPS_ID"},
    ]
    assert [a["id"] for a in users["row_menu"]] == ["display_roles"]
    # Sibling screen unchanged — still has its own dialog + update_query.
    assert screens["users_props"]["update_query"] == "users_props_put"
    assert screens["users_props"]["dialog"]["tabs"][0]["fields"][0]["name"] == "USRP_ID"
    parse_screens(out)


def test_migrate_screens_skips_promotion_when_parent_has_own_dialog() -> None:
    """Promotion is a *fallback*: it only fires when the parent has no own dialog
    (``tbl_frm_id`` was unset). If the parent already has a dialog, leave the row menu alone —
    the ctx menu entry is genuine drill-away."""
    table_rows = [
        {"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users",
         "tbl_editable": "Y", "tbl_uploadable": "N", "tbl_audit": None, "tbl_auto_load": "Y",
         "tbl_frm_id": 7},  # has own dialog
    ]
    frm_rows = [{"frm_id": 7, "dlg_id": 1, "frm_query_id": 10, "frm_label": "User"}]
    tab_rows = [{"frm_id": 7, "tab_id": 1, "tab_seq": 1, "tab_label": "G", "tab_cols": 1,
                 "tab_disable_add": "N", "tab_disable_edit": "N"}]
    col_rows = [{"frm_id": 7, "col_id": 1, "tab_id": 1, "col_seq": 1, "col_target": "USR_ID",
                 "col_visible": "Y", "col_disabled": "N", "col_required": "Y"}]
    promotable = {1: {"action_id": "display_properties", "target_qid": 20, "binds": [], "target_pool": "nomasx1"}}
    row_menus = {1: [{"id": "display_properties", "type": "navigate", "to": "users_props_get",
                       "label": "Display Properties"}]}
    out = migrate_screens(
        table_rows, frm_rows=frm_rows, tab_rows=tab_rows, col_rows=col_rows,
        sql_rows=_SCR_SQL, row_menus=row_menus, promotable_dialogs=promotable,
        app_name="nomasx1",
    )
    users = out["screens"]["nomasx1"]["users"]
    # Parent has its own dialog → no promotion; row_menu kept as-is.
    assert "row_click_screen" not in users
    assert [a["id"] for a in users["row_menu"]] == ["display_properties"]


def test_migrate_screens_drops_self_referential_row_menu_items() -> None:
    """v1's contextual menu often carried a "Display Properties" entry pointing at the same
    FormsDialog the row click also opens. In v2 a row click opens the Screen's own dialog, so a
    NavigateAction targeting this screen's own ``read_query`` (on the same connector) is
    redundant — strip it. A genuine drill-away (different ``to``, or a cross-connector hop) survives."""
    table_rows = [
        # Screen "users" reads from query 10 → migrate_sql_queries names that `users_get`
        # (the raw v1 `query_crud` rides through). The redundant ctx menu item targets the
        # same query — drop it. The other items survive.
        {"tbl_id": 1, "tbl_db_name": "users", "tbl_query_id": 10, "tbl_label": "Users"},
    ]
    row_menus = {
        1: [
            # Redundant — same connector (implicit), same query as the screen's own read.
            {"id": "display_properties", "type": "navigate", "to": "users_get", "label": "Display Properties"},
            # Survives — different query on the same connector.
            {"id": "display_roles", "type": "navigate", "to": "roles_get", "label": "Display Roles"},
            # Survives — same query but a *different* connector (cross-pool drill).
            {"id": "see_in_other", "type": "navigate", "to": "users_get", "connector": "jdedwards",
             "label": "See in JDE"},
        ],
    }
    out = migrate_screens(table_rows, sql_rows=_SCR_SQL, row_menus=row_menus, app_name="nomasx1")
    screens = out["screens"]["nomasx1"]
    assert [a["id"] for a in screens["users"]["row_menu"]] == ["display_roles", "see_in_other"]
    parse_screens(out)


# --------------------------------------------------------------------------- #
# DB readers (against a minimal v1 schema in SQLite)
# --------------------------------------------------------------------------- #

_V1_SCHEMA = [
    "CREATE TABLE ly_query (query_id INTEGER PRIMARY KEY, query_label TEXT, query_type TEXT)",
    "CREATE TABLE ly_qry_sql (query_id INTEGER, query_dbtype TEXT, query_crud TEXT, query_pool TEXT, query_sqlquery TEXT, query_orderby TEXT)",
    "CREATE TABLE ly_applications (apps_name TEXT, apps_pool TEXT, apps_dbtype TEXT, apps_jdbc TEXT, apps_user TEXT, apps_password TEXT, apps_host TEXT, apps_port INTEGER, apps_database TEXT, apps_pool_min INTEGER, apps_pool_max INTEGER, apps_limit INTEGER)",
    "CREATE TABLE ly_dictionary (dd_id TEXT PRIMARY KEY, dd_label TEXT, dd_type TEXT, dd_rules TEXT, dd_rules_values TEXT, dd_default TEXT)",
    "CREATE TABLE ly_dictionary_l (dd_id TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_enum (enum_id INTEGER PRIMARY KEY, enum_label TEXT)",
    "CREATE TABLE ly_enum_val (enum_id INTEGER, val_enum TEXT, val_label TEXT)",
    "CREATE TABLE ly_enum_val_l (enum_id INTEGER, val_enum TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_lookup (lkp_id INTEGER PRIMARY KEY, lkp_description TEXT, lkp_query_id INTEGER, lkp_dd_id TEXT, lkp_dd_label TEXT, lkp_dd_group TEXT)",
    "CREATE TABLE ly_tables (tbl_id INTEGER PRIMARY KEY, tbl_db_name TEXT, tbl_query_id INTEGER, tbl_label TEXT, tbl_auto_load TEXT, tbl_editable TEXT, tbl_uploadable TEXT, tbl_audit TEXT, tbl_frm_id INTEGER, tbl_ctx_id INTEGER)",
    "CREATE TABLE ly_tbl_col (tbl_id INTEGER, col_id INTEGER, col_seq INTEGER, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT, col_filter TEXT, col_key TEXT, col_cdn_id INTEGER)",
    "CREATE TABLE ly_dialogs (dlg_id INTEGER PRIMARY KEY, dlg_label TEXT)",
    "CREATE TABLE ly_dlg_frm (frm_id INTEGER PRIMARY KEY, dlg_id INTEGER, frm_query_id INTEGER, frm_label TEXT)",
    "CREATE TABLE ly_dlg_tab (frm_id INTEGER, tab_id INTEGER, tab_seq INTEGER, tab_label TEXT, tab_cols INTEGER, tab_disable_add TEXT, tab_disable_edit TEXT)",
    "CREATE TABLE ly_dlg_tab_l (frm_id INTEGER, tab_id INTEGER, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_dlg_col (frm_id INTEGER, col_id INTEGER, tab_id INTEGER, col_seq INTEGER, col_colspan INTEGER, col_component TEXT, col_component_id INTEGER, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT, col_disabled TEXT, col_required TEXT, col_default TEXT, col_key TEXT, col_cdn_id INTEGER)",
    "CREATE TABLE ly_tbl_filters (tbl_id INTEGER, col_id INTEGER, flt_id INTEGER, flt_type TEXT, flt_source TEXT, flt_target TEXT, flt_value TEXT)",
    "CREATE TABLE ly_dlg_filters (frm_id INTEGER, col_id INTEGER, flt_id INTEGER, flt_type TEXT, flt_source TEXT, flt_target TEXT, flt_value TEXT)",
    "CREATE TABLE ly_cdn_params (cdn_id INTEGER, cdn_params_id INTEGER, cdn_seq INTEGER, cdn_dd_id TEXT, cdn_operator TEXT, cdn_value TEXT, cdn_logical TEXT, cdn_group INTEGER)",
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
            text("INSERT INTO ly_applications (apps_name, apps_pool, apps_dbtype, apps_jdbc, apps_user, apps_password, apps_host, apps_port, apps_database, apps_pool_min, apps_pool_max, apps_limit)"
                 " VALUES (:n, :p, :db, :j, :u, :pw, :h, :port, :d, :mn, :mx, :lim)"),
            [
                {"n": "Framework", "p": "default", "db": "postgres", "j": None, "u": "liberty", "pw": "ENC:fw", "h": "fw.example", "port": 5432, "d": "libnsx1", "mn": 1, "mx": 10, "lim": 1000},
                {"n": "NOMASX1", "p": "nomasx1", "db": "postgres", "j": None, "u": "nomasx1", "pw": "ENC:nx", "h": "db.example", "port": 5432, "d": "nomasx1", "mn": 2, "mx": 20, "lim": 5000},
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
            text("INSERT INTO ly_tables (tbl_id, tbl_db_name, tbl_query_id, tbl_label, tbl_auto_load, tbl_editable, tbl_uploadable, tbl_audit, tbl_frm_id)"
                 " VALUES (:i, :n, :q, :l, :al, :e, :u, :a, :f)"),
            [{"i": 5, "n": "security_users", "q": 1, "l": "Users", "al": "Y", "e": "Y", "u": "N", "a": "Y", "f": 7}],
        )
        await conn.execute(
            text("INSERT INTO ly_tbl_col (tbl_id, col_id, col_seq, col_dd_id, col_label, col_target, col_type, col_visible, col_filter, col_key, col_cdn_id)"
                 " VALUES (:t, :c, :s, :dd, :lab, :tgt, :ty, :v, :f, :k, :cdn)"),
            [
                {"t": 5, "c": 1, "s": 1, "dd": "USR_ID", "lab": None, "tgt": "USR_ID", "ty": "number", "v": "Y", "f": "Y", "k": "Y", "cdn": None},
                {"t": 5, "c": 2, "s": 2, "dd": None, "lab": "User Name", "tgt": "USR_NAME", "ty": "text", "v": "Y", "f": "N", "k": None, "cdn": 1},  # → ly_cdn_params cdn_id 1
                {"t": 5, "c": 3, "s": 3, "dd": None, "lab": "Password", "tgt": "USR_PWD", "ty": "password", "v": "N", "f": None, "k": "N", "cdn": None},
            ],
        )
        await conn.execute(
            text("INSERT INTO ly_tbl_filters (tbl_id, col_id, flt_id, flt_type, flt_source, flt_target, flt_value)"
                 " VALUES (:t, :c, :i, :ty, :src, :tgt, :v)"),
            # USR_NAME's filter dropdown cascades from USR_ID (its lookup-result column UN_REF matches it)
            [{"t": 5, "c": 2, "i": 1, "ty": "DD", "src": "USR_ID", "tgt": "UN_REF", "v": None}],
        )
        # conditional rendering: USR_NAME (col 2, col_cdn_id = 1) shows only when the USR_ID filter is unset or == '42'
        await conn.execute(
            text("INSERT INTO ly_cdn_params (cdn_id, cdn_params_id, cdn_seq, cdn_dd_id, cdn_operator, cdn_value, cdn_logical, cdn_group)"
                 " VALUES (:i, :p, :s, :dd, :op, :v, :lg, :g)"),
            [
                {"i": 1, "p": 1, "s": 1, "dd": "USR_ID", "op": "EQUAL", "v": "42", "lg": "OR", "g": 0},
                {"i": 1, "p": 2, "s": 2, "dd": "USR_ID", "op": "EMPTY", "v": None, "lg": "OR", "g": 0},
            ],
        )
        # Dialog stack: ly_dialogs → ly_dlg_frm → ly_dlg_tab (+ _l) → ly_dlg_col → ly_dlg_filters.
        # Screen 5 ("Users") points at frm 7 → the dialog with one tab and one field.
        await conn.execute(
            text("INSERT INTO ly_dialogs (dlg_id, dlg_label) VALUES (:i, :l)"),
            [{"i": 1, "l": "Users"}],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_frm (frm_id, dlg_id, frm_query_id, frm_label) VALUES (:i, :d, :q, :l)"),
            [{"i": 7, "d": 1, "q": 2, "l": "Delete Form"}],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_tab (frm_id, tab_id, tab_seq, tab_label, tab_cols, tab_disable_add, tab_disable_edit)"
                 " VALUES (:f, :t, :s, :l, :c, :a, :e)"),
            [{"f": 7, "t": 1, "s": 1, "l": "General", "c": 2, "a": "N", "e": "N"}],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_tab_l (frm_id, tab_id, lng_id, lng_label) VALUES (:f, :t, :lng, :lab)"),
            [{"f": 7, "t": 1, "lng": "fr", "lab": "Général"}],
        )
        await conn.execute(
            text("INSERT INTO ly_dlg_col (frm_id, col_id, tab_id, col_seq, col_colspan, col_component, col_dd_id, col_label, col_target, col_type, col_visible, col_disabled, col_required, col_default, col_key, col_cdn_id)"
                 " VALUES (:f, :c, :ta, :s, :cs, :cm, :dd, :lab, :tgt, :ty, :v, :di, :rq, :de, :k, :cdn)"),
            [
                # field 1 — the always-visible identifier; carries the USR_ID dd → also acts as the
                # cdn predicate's "field" target when resolving col_cdn_id on the next field.
                {"f": 7, "c": 1, "ta": 1, "s": 1, "cs": 2, "cm": "input", "dd": "USR_ID", "lab": "Id", "tgt": "USR_ID",
                 "ty": "integer", "v": "Y", "di": "N", "rq": "Y", "de": "0", "k": "Y", "cdn": None},
                # field 2 — visible only when USR_ID == '42' (matches ly_cdn_params cdn_id 1 already
                # seeded above for the grid columns; same cdn id is reused — exactly v1's pattern
                # of sharing condition graphs across table + dialog screens).
                {"f": 7, "c": 2, "ta": 1, "s": 2, "cs": None, "cm": "input", "dd": None, "lab": "Extra",
                 "tgt": "USR_EXTRA", "ty": "text", "v": "Y", "di": "N", "rq": "N", "de": None, "k": "N", "cdn": 1},
            ],
        )
        # Per-field param binds (v1 ly_dlg_filters): one VALUE literal + one DD column-bind on the same field.
        await conn.execute(
            text("INSERT INTO ly_dlg_filters (frm_id, col_id, flt_id, flt_type, flt_source, flt_target, flt_value)"
                 " VALUES (:f, :c, :i, :ty, :src, :tgt, :v)"),
            [
                {"f": 7, "c": 1, "i": 1, "ty": "VALUE", "src": None, "tgt": "STATUS", "v": "A"},
                {"f": 7, "c": 1, "i": 2, "ty": "DD", "src": "USR_APPS_ID", "tgt": "ROL_APPS_ID", "v": None},
            ],
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
    assert pools["nomasx1"]["url"] == "postgresql+asyncpg://nomasx1@db.example:5432/nomasx1"
    assert pools["nomasx1"]["password"] == "ENC:nx"  # apps_password carried over verbatim


@pytest.mark.asyncio
async def test_read_column_hints(v1_engine) -> None:
    tbl_cols, dlg_cols = await read_column_hints(v1_engine)
    assert {(r["query_id"], r["col_target"]) for r in tbl_cols} == {(1, "USR_ID"), (1, "USR_NAME"), (1, "USR_PWD")}
    # The dialog has two fields now (the second one carries col_cdn_id to exercise slice 3's
    # conditional-visibility wiring) — both live under frm 7, joined to query 2 by frm_query_id.
    assert {(r["query_id"], r["col_target"]) for r in dlg_cols} == {(2, "USR_ID"), (2, "USR_EXTRA")}
    hints = migrate_column_hints(tbl_cols, dlg_cols)
    assert [h["name"] for h in hints[1]] == ["USR_ID", "USR_NAME", "USR_PWD"]
    assert hints[1][0] == {"name": "USR_ID", "filter": True, "format": "number"}  # col_dd_id == name → no `dd`; col_filter 'Y'
    assert hints[1][2] == {"name": "USR_PWD", "label": "Password", "hidden": True, "format": "password"}  # col_visible 'N', col_filter null
    assert hints[2] == [
        # col_dd_id="USR_ID" matches `name` → no `dd`; col_type "integer" → format
        {"name": "USR_ID", "label": "Id", "format": "integer"},
        # second field: col_dd_id NULL → no `dd`; col_type "text" gets normalised away (the
        # migrator drops dd_type "text" since it's the natural default)
        {"name": "USR_EXTRA", "label": "Extra"},
    ]


@pytest.mark.asyncio
async def test_read_table_filters(v1_engine) -> None:
    tbl_flt, dlg_flt = await read_table_filters(v1_engine)
    # the seeded ly_tbl_filters row, joined to its column's col_target and its query id
    assert [{"query_id": r["query_id"], "col_target": r["col_target"], "src": r["src"], "tgt": r["tgt"]} for r in tbl_flt] \
        == [{"query_id": 1, "col_target": "USR_NAME", "src": "USR_ID", "tgt": "UN_REF"}]
    # ly_dlg_filters seed: the VALUE row (flt_source NULL) is filtered out by _DLG_FILTERS' WHERE;
    # the DD row (col USR_ID on frm 7 → query 2) survives.
    assert [{"query_id": r["query_id"], "col_target": r["col_target"], "src": r["src"], "tgt": r["tgt"]} for r in dlg_flt] \
        == [{"query_id": 2, "col_target": "USR_ID", "src": "USR_APPS_ID", "tgt": "ROL_APPS_ID"}]
    assert migrate_table_filters(tbl_flt, dlg_flt) == {
        1: {"USR_NAME": [{"source": "USR_ID", "column": "UN_REF"}]},
        2: {"USR_ID": [{"source": "USR_APPS_ID", "column": "ROL_APPS_ID"}]},
    }


@pytest.mark.asyncio
async def test_read_column_conditions(v1_engine) -> None:
    from liberty.migrations import read_column_hints
    tbl_cols, dlg_cols = await read_column_hints(v1_engine)
    params = await read_column_conditions(v1_engine)
    assert {(r["cdn_id"], r["cdn_operator"]) for r in params} == {(1, "EQUAL"), (1, "EMPTY")}
    # USR_NAME (col_cdn_id 1 on the table → query 1) and USR_EXTRA (col_cdn_id 1 on the dialog →
    # query 2) share the same cdn graph — both distil to "shows unless USR_ID ≠ '42'", proving the
    # cdn graph is reusable across table+dialog screens (exactly v1's pattern).
    assert migrate_column_visibility(tbl_cols, dlg_cols, params) == {
        1: {"USR_NAME": [{"field": "USR_ID", "value": ["42"]}]},
        2: {"USR_EXTRA": [{"field": "USR_ID", "value": ["42"]}]},
    }


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
    enum_rows, enum_val_rows, enum_val_l_rows, lookup_rows, sql_rows, filter_rows, lkp_param_rows = await read_dictionary_rules(v1_engine)
    assert [r["enum_id"] for r in enum_rows] == [1]
    assert {(r["enum_id"], r["val_enum"]) for r in enum_val_rows} == {(1, "A"), (1, "I")}
    assert {r["lkp_id"] for r in lookup_rows} == {1}
    # The fake v1 db has no ly_dictionary_filters / ly_lkp_params tables — _rows_or_empty returns [].
    assert filter_rows == [] and lkp_param_rows == []
    out = migrate_dictionary(
        dict_rows, dict_l_rows, enum_rows, enum_val_rows, enum_val_l_rows, lookup_rows, sql_rows, filter_rows, lkp_param_rows,
        connector_name="db",
    )
    d = parse_dictionary(tomllib.loads(render_toml(out)))
    sec = d.connectors["db"]
    # enum migrated, with the fr translation on the Active value
    assert sec.enums["1"].label == "User Status"
    vals = {v.value: v for v in sec.enums["1"].values}
    assert vals["A"].label == "Active" and vals["A"].label_for("fr") == "Actif"
    assert vals["I"].label_for("fr") == "Inactive"  # no fr translation → falls back to the default
    # lookup migrated, with lkp_query_id=1 → the read-variant name migrate_sql_queries gives that query;
    # `connector` is the slug of that query's pool ("default" here) — may differ from the dict section
    assert sec.lookups["1"].query == "users_list_select" and sec.lookups["1"].connector == "default"
    assert sec.lookups["1"].value == "USR_ID" and sec.lookups["1"].label == "USR_NAME"
    # the entries' rules round-trip; resolve_rule returns the right wire shape
    usr_id_rule = d.resolve_rule(sec.entries["USR_ID"], connector="db", language="en")
    assert usr_id_rule == {
        "kind": "lookup", "connector": "default", "query": "users_list_select", "value": "USR_ID", "label": "USR_NAME",
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
async def test_read_screens(v1_engine) -> None:
    """End-to-end read_screens + migrate_screens against the SQLite fixture: the seeded
    ``ly_tables`` row 5 has ``tbl_frm_id=7``, which fans out to one tab with one field plus
    two ly_dlg_filters bindings (one VALUE literal, one DD column-source)."""
    rows = await read_screens(v1_engine)
    table_rows, dialog_rows, frm_rows, tab_rows, tab_l_rows, col_rows, filter_rows, sql_rows = rows
    assert {r["tbl_id"] for r in table_rows} == {5}
    assert {r["tbl_db_name"] for r in table_rows} == {"security_users"}
    assert {r["dlg_id"] for r in dialog_rows} == {1}
    assert {r["frm_id"] for r in frm_rows} == {7}
    assert {(r["frm_id"], r["tab_id"]) for r in tab_rows} == {(7, 1)}
    assert {(r["frm_id"], r["tab_id"], r["lng_id"]) for r in tab_l_rows} == {(7, 1, "fr")}
    assert {(r["frm_id"], r["col_id"], r["col_target"]) for r in col_rows} == {(7, 1, "USR_ID"), (7, 2, "USR_EXTRA")}
    assert {(r["frm_id"], r["col_id"], r["flt_type"]) for r in filter_rows} == {(7, 1, "VALUE"), (7, 1, "DD")}
    assert {r["query_id"] for r in sql_rows} == {1, 2}  # the join feeds CRUD → v2 name resolution
    # The cdn graph (ly_cdn_params) is read separately — same source the grid uses.
    cdn_params = await read_column_conditions(v1_engine)
    # Build the screens.toml fragment + round-trip through the screens schema (pass cdn_params so
    # the field's col_cdn_id resolves to visible_when).
    out = migrate_screens(*rows, cdn_param_rows=cdn_params, app_name="nomasx1")
    sf = parse_screens(out)
    screens = sf.screens["nomasx1"]
    s = screens["security_users"]
    assert s.read_query == "users_list_select"
    # tbl_query_id=1 has only a SELECT companion in ly_qry_sql → no update/insert/delete refs.
    assert s.update_query is None and s.insert_query is None and s.delete_query is None
    assert s.audit is True and s.auto_load is True
    # Pool of query 1 is 'default' (not the app 'nomasx1') → `connector` is explicitly set.
    assert s.connector == "default"
    # Dialog walks ly_dlg_tab (+ _l) + ly_dlg_col + ly_dlg_filters.
    assert s.dialog is not None
    assert [t.id for t in s.dialog.tabs] == ["general"]
    tab = s.dialog.tabs[0]
    assert tab.label == "General" and tab.cols == 2 and tab.l == {"fr": "Général"}
    assert [f.name for f in tab.fields] == ["USR_ID", "USR_EXTRA"]
    field = tab.fields[0]
    # col_label='Id' overrides the dictionary; col_dd_id == name → `dd` left unset (falls back to `name`).
    assert field.label == "Id" and field.dd is None and field.required is True and field.colspan == 2
    assert field.default == "0"
    # field 1 has no col_cdn_id → no visible_when
    assert field.visible_when == []
    # Both ParamBind flavours preserved: VALUE → {param, value}, DD → {param, source}.
    binds = [{"param": b.param, "value": b.value, "source": b.source} for b in field.lookup_param_binds]
    assert binds == [
        {"param": "STATUS", "value": "A", "source": None},
        {"param": "ROL_APPS_ID", "value": None, "source": "USR_APPS_ID"},
    ]
    # field 2 — the conditional one: col_cdn_id=1 → ly_cdn_params cdn_id 1 (USR_ID EQUAL '42'
    # OR EMPTY). The EMPTY predicate is v2's default (no constraint); only EQUAL contributes a value.
    extra = tab.fields[1]
    assert extra.name == "USR_EXTRA"
    assert [{"field": c.field, "value": c.value} for c in extra.visible_when] == [
        {"field": "USR_ID", "value": ["42"]},
    ]


@pytest.mark.asyncio
async def test_read_applications_column_hints_dictionary_menus_missing_tables(tmp_path) -> None:
    from liberty.migrations import read_dictionary_rules, read_screens
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'no_meta.db'}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE ly_query (query_id INTEGER PRIMARY KEY)"))
    try:
        assert await read_applications(engine) == []           # no ly_applications → []
        assert await read_column_hints(engine) == ([], [])     # no ly_tbl_col/ly_dlg_col → ([], [])
        assert await read_dictionary(engine) == ([], [])       # no ly_dictionary/ly_dictionary_l → ([], [])
        assert await read_menus(engine) == ([], [], [], [], [])  # no ly_menus/… → all empty
        assert await read_dictionary_rules(engine) == ([], [], [], [], [], [], [])  # no ly_enum/ly_lookup/ly_dictionary_filters/ly_lkp_params → all empty
        # ly_tables / ly_dialogs / ly_dlg_frm / ly_dlg_tab / _l / ly_dlg_col / ly_dlg_filters / ly_qry_sql → all empty
        assert await read_screens(engine) == ([], [], [], [], [], [], [], [])
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
    # ly_applications scaffolded [pools.nomasx1] with a real URL (no password in it) + a separate
    # `password` field (v1's ENC: value carried over); v1's `default` pool was skipped, so
    # [pools.default] is still the env-var stub.
    assert "nomasx1" in cfg.pools
    assert "postgresql+asyncpg://nomasx1@db.example:5432/nomasx1" in text_out
    assert 'password = "ENC:nx"' in text_out  # carried over from apps_password, kept out of the URL
    assert "${LIBERTY_DB_URL_DEFAULT}" in text_out  # the default pool kept its env-var stub
    # ly_tbl_col → the migrated SELECT carries column display hints
    sql_conn = cfg.connectors["default"]
    assert isinstance(sql_conn, SqlConnectorConfig)
    q1 = next(q for q in sql_conn.queries if q.name == "users_list_select")
    assert [c.name for c in q1.columns] == ["USR_ID", "USR_NAME", "USR_PWD"]
    assert q1.columns[2].hidden is True and q1.columns[1].label == "User Name"
    # ly_tbl_filters → the USR_NAME column's filter dropdown cascades from USR_ID
    assert [{"source": d.source, "column": d.column} for d in q1.columns[1].filter_from] == [{"source": "USR_ID", "column": "UN_REF"}]
    # ly_tbl_col_cdn → USR_NAME shows unless the USR_ID filter is set to something other than '42'
    assert [r.as_dict() for r in q1.columns[1].visible_when_rules] == [{"field": "USR_ID", "value": ["42"]}]


def test_cli_all_to_stdout(tmp_path, capsys) -> None:
    url = _make_v1_db(tmp_path)
    assert migrate_main(["all", "--source-url", url]) == 0
    out = capsys.readouterr().out
    assert "fill in these placeholders" in out  # ${LIBERTY_DB_URL_DEFAULT} (the framework pool stub)
    assert "ENC:" in out  # the migrated pool's password (apps_password ENC: value, kept out of the URL)
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


def test_cli_screen(tmp_path) -> None:
    """End-to-end ``liberty-migrate screen`` against the SQLite fixture."""
    url = _make_v1_db(tmp_path)
    out = tmp_path / "screens.toml"
    assert migrate_main(["screen", "--source-url", url, "--connector", "nomasx1", "-o", str(out)]) == 0
    txt = out.read_text()
    assert txt.startswith("# migrated:")
    assert "1 screen(s) for [screens.nomasx1]" in txt
    assert "1 with dialog" in txt and "1 with audit" in txt
    # Pool of query 1 is 'default', not the app 'nomasx1' → flagged as cross-connector.
    assert "1 cross-connector" in txt
    assert "2 param-bind(s)" in txt
    # The CLI also reports conditional field counts (slice 3) — the seeded USR_EXTRA field's
    # col_cdn_id pulled visible_when through.
    assert "1 conditional field(s)" in txt
    # Nested tabs (FormsDialog / FormsTable inside a FormsDialog) — the seed has none, so both
    # counters read zero. Asserts the new line is in the summary regardless.
    assert "0 nested form tab(s), 0 nested table tab(s)" in txt
    # Slice 6b — the seed has no ly_ctxmenus / ly_ctx_val tables (an older v1 schema is allowed),
    # so the row-menu metric reads zero. Asserts the new line is in the summary regardless.
    assert "0 with row-menu" in txt
    # Parses through both tomllib (comments are fine) and the screens schema.
    sf = parse_screens(tomllib.loads(txt))
    screens = sf.screens["nomasx1"]
    s = screens["security_users"]
    assert s.read_query == "users_list_select"
    assert s.connector == "default" and s.audit is True and s.auto_load is True
    assert s.dialog is not None
    tab = s.dialog.tabs[0]
    assert tab.label == "General" and tab.l == {"fr": "Général"}
    field = tab.fields[0]
    assert field.name == "USR_ID" and field.required is True
    # Both ParamBind shapes round-trip.
    assert [(b.param, b.value, b.source) for b in field.lookup_param_binds] == [
        ("STATUS", "A", None),
        ("ROL_APPS_ID", None, "USR_APPS_ID"),
    ]
    # Field 2 carries the migrated conditional visibility — the schema round-trips it cleanly.
    extra = tab.fields[1]
    assert extra.name == "USR_EXTRA"
    assert [(c.field, c.value) for c in extra.visible_when] == [("USR_ID", ["42"])]


def test_cli_screen_merges_into_existing_file(tmp_path) -> None:
    """Re-running ``liberty-migrate screen --connector <other-app> -o <existing>`` merges the
    new app's section into the existing screens.toml — the previous app's section survives.
    The user lost their nomajde screens when we re-ran nomasx1 because the previous behaviour
    was to *replace* the file; this test pins the new merge behaviour so it can't regress."""
    url = _make_v1_db(tmp_path)
    out = tmp_path / "screens.toml"

    # First write — nomasx1 → fresh file with one screen.
    assert migrate_main(["screen", "--source-url", url, "--connector", "nomasx1", "-o", str(out)]) == 0
    first = out.read_text()
    assert "1 screen(s) for [screens.nomasx1]" in first
    assert "[screens.nomasx1.security_users]" in first

    # Inject a hand-added section under a *different* app name into the file to simulate the
    # multi-app deployment case (the user had nomajde screens here from an earlier migration).
    out.write_text(first + "\n[screens.nomajde.role_management]\nid = \"role_management\"\nread_query = \"roles_get\"\n")

    # Second write — re-run for the same nomasx1. With merge mode, nomasx1's section gets
    # updated (replaced with the latest migration) but the hand-added nomajde section survives.
    assert migrate_main(["screen", "--source-url", url, "--connector", "nomasx1", "-o", str(out)]) == 0
    merged = out.read_text()
    assert "[screens.nomasx1.security_users]" in merged
    assert "[screens.nomajde.role_management]" in merged
    assert 'read_query = "roles_get"' in merged
    # The merge re-parses through the screens schema — both apps land in the final result.
    sf = parse_screens(tomllib.loads(merged))
    assert set(sf.screens) == {"nomasx1", "nomajde"}
    assert "role_management" in sf.screens["nomajde"]
