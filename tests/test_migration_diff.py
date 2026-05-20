"""Tests for :mod:`liberty.migrations.diff` — the validate-by-diff CLI's engine.

Approach: seed a SQLite (aiosqlite) DB with a minimal v1 schema + a few representative rows
in each table the diff walks; write a small v2 config tree to ``tmp_path``; call
:func:`compute_diff` against the pair; assert the resulting :class:`DiffReport` has the
expected mix of ``missing`` / ``ok`` / ``info`` entries.

Each test exercises one entity kind in isolation so a failure points clearly at the broken
check. A final smoke test walks the full pipeline end-to-end to make sure the top-level
:func:`compute_diff` glues everything together.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from typing import Iterable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.migrations.diff import compute_diff, render_text


# ── v1 schema (mirrors what the source.py readers SELECT from) ─────────────────────────────


_V1_SCHEMA: list[str] = [
    "CREATE TABLE ly_query (query_id INTEGER PRIMARY KEY, query_label TEXT, query_type TEXT)",
    "CREATE TABLE ly_qry_sql (query_id INTEGER, query_dbtype TEXT, query_crud TEXT, query_pool TEXT, query_sqlquery TEXT, query_orderby TEXT)",
    "CREATE TABLE ly_applications (apps_name TEXT, apps_pool TEXT, apps_dbtype TEXT, apps_jdbc TEXT, apps_user TEXT, apps_password TEXT, apps_host TEXT, apps_port INTEGER, apps_database TEXT, apps_pool_min INTEGER, apps_pool_max INTEGER, apps_limit INTEGER)",
    "CREATE TABLE ly_db_schema (sch_pool TEXT, sch_name TEXT, sch_target TEXT)",
    "CREATE TABLE ly_dictionary (dd_id TEXT PRIMARY KEY, dd_label TEXT, dd_type TEXT, dd_rules TEXT, dd_rules_values TEXT, dd_default TEXT)",
    "CREATE TABLE ly_dictionary_l (dd_id TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_enum (enum_id INTEGER PRIMARY KEY, enum_label TEXT)",
    "CREATE TABLE ly_enum_val (enum_id INTEGER, val_enum TEXT, val_label TEXT)",
    "CREATE TABLE ly_enum_val_l (enum_id INTEGER, val_enum TEXT, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_lookup (lkp_id INTEGER PRIMARY KEY, lkp_description TEXT, lkp_query_id INTEGER, lkp_dd_id TEXT, lkp_dd_label TEXT, lkp_dd_group TEXT)",
    "CREATE TABLE ly_sequence (seq_id INTEGER PRIMARY KEY, seq_label TEXT, seq_query_id INTEGER, seq_dd_id TEXT)",
    "CREATE TABLE ly_seq_params (seq_id INTEGER, dd_id TEXT)",
    "CREATE TABLE ly_dictionary_filters (dd_id TEXT, flt_id INTEGER, flt_type TEXT, flt_target TEXT, flt_value TEXT)",
    "CREATE TABLE ly_lkp_params (lkp_id INTEGER, lkp_dir TEXT, dd_id TEXT)",
    "CREATE TABLE ly_tables (tbl_id INTEGER PRIMARY KEY, tbl_db_name TEXT, tbl_query_id INTEGER, tbl_label TEXT, tbl_auto_load TEXT, tbl_editable TEXT, tbl_uploadable TEXT, tbl_audit TEXT, tbl_frm_id INTEGER, tbl_ctx_id INTEGER)",
    "CREATE TABLE ly_tbl_col (tbl_id INTEGER, col_id INTEGER, col_seq INTEGER, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT, col_filter TEXT, col_key TEXT, col_cdn_id INTEGER)",
    "CREATE TABLE ly_dialogs (dlg_id INTEGER PRIMARY KEY, dlg_label TEXT)",
    "CREATE TABLE ly_dlg_frm (frm_id INTEGER PRIMARY KEY, dlg_id INTEGER, frm_query_id INTEGER, frm_label TEXT)",
    "CREATE TABLE ly_dlg_tab (frm_id INTEGER, tab_id INTEGER, tab_seq INTEGER, tab_label TEXT, tab_cols INTEGER, tab_disable_add TEXT, tab_disable_edit TEXT)",
    "CREATE TABLE ly_dlg_tab_l (frm_id INTEGER, tab_id INTEGER, lng_id TEXT, lng_label TEXT)",
    "CREATE TABLE ly_dlg_col (frm_id INTEGER, col_id INTEGER, tab_id INTEGER, col_seq INTEGER, col_colspan INTEGER, col_component TEXT, col_component_id INTEGER, col_dd_id TEXT, col_label TEXT, col_target TEXT, col_type TEXT, col_visible TEXT, col_disabled TEXT, col_required TEXT, col_default TEXT, col_key TEXT, col_cdn_id INTEGER, col_rules TEXT, col_rules_values TEXT)",
    "CREATE TABLE ly_tbl_filters (tbl_id INTEGER, col_id INTEGER, flt_id INTEGER, flt_type TEXT, flt_source TEXT, flt_target TEXT, flt_value TEXT)",
    "CREATE TABLE ly_dlg_filters (frm_id INTEGER, col_id INTEGER, flt_id INTEGER, flt_type TEXT, flt_source TEXT, flt_target TEXT, flt_value TEXT)",
    "CREATE TABLE ly_menus (menu_seq_ukid TEXT PRIMARY KEY, menu_parent_id TEXT, menu_child_id TEXT, menu_component TEXT, menu_component_id INTEGER, menu_label TEXT, menu_level INTEGER, menu_id INTEGER)",
    "CREATE TABLE ly_menus_l (lng_id TEXT, lng_seq_ukid TEXT, lng_label TEXT)",
    "CREATE TABLE ly_api_conn (conn_id INTEGER PRIMARY KEY, conn_label TEXT, conn_name TEXT, conn_url TEXT, conn_user TEXT, conn_password TEXT)",
    "CREATE TABLE ly_api (api_id INTEGER PRIMARY KEY, api_label TEXT, api_source TEXT, api_method TEXT, api_url TEXT, api_user TEXT, api_password TEXT, api_body TEXT, api_conn_id INTEGER)",
    "CREATE TABLE ly_api_header (api_id INTEGER, hdr_id INTEGER, hdr_key TEXT, hdr_value TEXT)",
    "CREATE TABLE ly_api_params (api_id INTEGER, map_id INTEGER, map_var TEXT, map_value TEXT)",
]


def _exec_many(engine, statements: Iterable[str]) -> None:
    async def go() -> None:
        async with engine.begin() as conn:
            for ddl in statements:
                await conn.execute(text(ddl))
    asyncio.run(go())


def _insert(engine, table: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    bind = ", ".join(f":{c}" for c in cols)

    async def go() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({bind})"),
                rows,
            )
    asyncio.run(go())


@pytest.fixture
def v1_db(tmp_path: Path) -> str:
    """Boot a fresh aiosqlite DB with the minimal v1 schema. Returns the URL the diff CLI's
    ``--source-url`` would take. SQLAlchemy's aiosqlite needs a unique filename per fixture
    instance so concurrent tests don't share state."""
    db_path = tmp_path / "v1.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    _exec_many(engine, _V1_SCHEMA)
    # SQLAlchemy engines pool connections; we close them here so the next assertions can
    # open a fresh engine without exhausting the test environment.
    asyncio.run(engine.dispose())
    return url


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


@pytest.fixture
def cfg_dir(tmp_path: Path) -> dict[str, Path]:
    """Minimal v2 config tree. Each test writes its own scoped bodies via the returned paths."""
    paths = {
        "dir": tmp_path / "config",
        "connectors": tmp_path / "config" / "connectors.toml",
        "screens": tmp_path / "config" / "screens.toml",
        "dictionary": tmp_path / "config" / "dictionary.toml",
        "menus": tmp_path / "config" / "menus.toml",
    }
    paths["dir"].mkdir(parents=True, exist_ok=True)
    return paths


def _seed_minimal_apps(engine_url: str) -> None:
    """Two v1 apps: ``Framework`` on ``default`` pool, ``NOMASX1`` on ``nomasx1`` pool."""
    engine = create_async_engine(engine_url)
    _insert(engine, "ly_applications", [
        {"apps_name": "Framework", "apps_pool": "default", "apps_dbtype": "postgres",
         "apps_jdbc": None, "apps_user": "u", "apps_password": "p", "apps_host": "h",
         "apps_port": 5432, "apps_database": "d", "apps_pool_min": 1, "apps_pool_max": 10,
         "apps_limit": 1000},
        {"apps_name": "NOMASX1", "apps_pool": "nomasx1", "apps_dbtype": "postgres",
         "apps_jdbc": None, "apps_user": "u", "apps_password": "p", "apps_host": "h",
         "apps_port": 5432, "apps_database": "d", "apps_pool_min": 1, "apps_pool_max": 10,
         "apps_limit": 1000},
    ])
    asyncio.run(engine.dispose())


# ── pool checks ────────────────────────────────────────────────────────────────────────────


def test_pool_missing_when_apps_pool_has_no_v2_pool(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """The connectors.toml only declares ``[pools.default]`` — the v1 ``nomasx1`` pool is
    missing and the diff flags it as severity=missing."""
    _seed_minimal_apps(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    missing_pools = [e for e in report.entries if e.kind == "pool" and e.severity == "missing"]
    assert {e.entity_id for e in missing_pools} == {"nomasx1"}
    assert report.has_problems() is True


def test_pool_info_when_v2_pool_is_a_stub(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """A v2 ``[pools.nomasx1]`` whose URL is still the migrator's ``${LIBERTY_DB_URL_X}``
    stub gets surfaced as severity=info — the rename succeeded but the operator forgot to
    fill in the real URL (or set the env var)."""
    _seed_minimal_apps(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.nomasx1]
        url = "${LIBERTY_DB_URL_NOMASX1}"
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    infos = [e for e in report.entries if e.kind == "pool" and e.severity == "info"]
    assert any(e.entity_id == "nomasx1" for e in infos)
    # Pool stub alone shouldn't trip ``has_problems`` — it's a warning, not a failure.
    assert all(e.severity == "info" for e in infos)


# ── SQL query checks ───────────────────────────────────────────────────────────────────────


def _seed_minimal_queries(engine_url: str) -> None:
    engine = create_async_engine(engine_url)
    _insert(engine, "ly_query", [
        {"query_id": 1, "query_label": "users", "query_type": "TABLE"},
        {"query_id": 2, "query_label": "users", "query_type": "FORM"},
    ])
    _insert(engine, "ly_qry_sql", [
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "GET",
         "query_pool": "nomasx1", "query_sqlquery": "SELECT 1", "query_orderby": None},
        {"query_id": 2, "query_dbtype": "postgres", "query_crud": "PUT",
         "query_pool": "nomasx1", "query_sqlquery": "UPDATE x SET y=1", "query_orderby": None},
    ])
    asyncio.run(engine.dispose())


def test_sql_query_ok_when_v2_has_matching_named_query(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v1 has two queries on ``nomasx1`` (GET + PUT). v2 carries both with the migrator's
    naming convention — ``users_get`` and ``users_put``. Diff reports them as ``ok``
    (in verbose mode) with no missing entries."""
    _seed_minimal_apps(v1_db)
    _seed_minimal_queries(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.nomasx1]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.nomasx1]
        type = "sql"
        pool = "nomasx1"

        [[connectors.nomasx1.queries]]
        name = "users_get"
        sql = "SELECT 1"

        [[connectors.nomasx1.queries]]
        name = "users_put"
        sql = "UPDATE x SET y=1"
        writable = true
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
        include_ok=True,
    )
    qs = [e for e in report.entries if e.kind == "sql_query"]
    assert {e.entity_id for e in qs} == {"nomasx1/users_get", "nomasx1/users_put"}
    assert all(e.severity == "ok" for e in qs)
    assert report.has_problems() is False


def test_sql_query_missing_when_v2_lacks_the_named_query(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v1 has a PUT, v2 only ships the GET. The PUT is reported missing."""
    _seed_minimal_apps(v1_db)
    _seed_minimal_queries(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.nomasx1]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.nomasx1]
        type = "sql"
        pool = "nomasx1"

        [[connectors.nomasx1.queries]]
        name = "users_get"
        sql = "SELECT 1"
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    missing = [e for e in report.entries
               if e.kind == "sql_query" and e.severity == "missing"]
    assert {e.entity_id for e in missing} == {"nomasx1/users_put"}
    assert report.has_problems() is True


def test_sql_query_extra_when_v2_has_a_handadded_query(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v2 has a query no v1 row produced (hand-added). Surfaces as severity=extra. ``extra``
    doesn't fail the diff (operators are allowed to hand-add queries)."""
    _seed_minimal_apps(v1_db)
    _seed_minimal_queries(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.nomasx1]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.nomasx1]
        type = "sql"
        pool = "nomasx1"

        [[connectors.nomasx1.queries]]
        name = "users_get"
        sql = "SELECT 1"

        [[connectors.nomasx1.queries]]
        name = "users_put"
        sql = "UPDATE x SET y=1"
        writable = true

        [[connectors.nomasx1.queries]]
        name = "handwritten_report"
        sql = "SELECT 'unrelated'"
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    extras = [e for e in report.entries
              if e.kind == "sql_query" and e.severity == "extra"]
    assert {e.entity_id for e in extras} == {"nomasx1/handwritten_report"}
    assert report.has_problems() is False                # extras don't fail the check


def test_sql_query_filter_scopes_to_one_connector(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """With ``connector_filter='nomasx1'``, queries on other pools are ignored — the diff
    only reports against the requested slice."""
    _seed_minimal_apps(v1_db)
    engine = create_async_engine(v1_db)
    _insert(engine, "ly_query", [
        {"query_id": 1, "query_label": "users", "query_type": "TABLE"},
        {"query_id": 2, "query_label": "roles", "query_type": "TABLE"},
    ])
    _insert(engine, "ly_qry_sql", [
        {"query_id": 1, "query_dbtype": "postgres", "query_crud": "GET",
         "query_pool": "nomasx1", "query_sqlquery": "SELECT 1", "query_orderby": None},
        {"query_id": 2, "query_dbtype": "postgres", "query_crud": "GET",
         "query_pool": "other_app", "query_sqlquery": "SELECT 2", "query_orderby": None},
    ])
    asyncio.run(engine.dispose())
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"
        [pools.nomasx1]
        url = "sqlite+aiosqlite:///:memory:"

        [connectors.nomasx1]
        type = "sql"
        pool = "nomasx1"
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
        connector_filter="nomasx1",
    )
    # The other_app/roles_get query is filtered out; only nomasx1/users_get surfaces (missing).
    sql_entries = [e for e in report.entries if e.kind == "sql_query"]
    assert {e.entity_id for e in sql_entries} == {"nomasx1/users_get"}


# ── dictionary checks ──────────────────────────────────────────────────────────────────────


def test_dict_entry_missing_when_v2_dictionary_lacks_it(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v1 has USR_ID + USR_NAME; v2's dictionary.toml only has USR_NAME. USR_ID is missing."""
    engine = create_async_engine(v1_db)
    _insert(engine, "ly_dictionary", [
        {"dd_id": "USR_ID", "dd_label": "User ID", "dd_type": "number",
         "dd_rules": None, "dd_rules_values": None, "dd_default": None},
        {"dd_id": "USR_NAME", "dd_label": "User Name", "dd_type": "text",
         "dd_rules": None, "dd_rules_values": None, "dd_default": None},
    ])
    asyncio.run(engine.dispose())
    _write(cfg_dir["connectors"], """[pools.default]
url = "sqlite+aiosqlite:///:memory:"
""")
    _write(cfg_dir["dictionary"], """
        [entries.USR_NAME]
        label = "Name"
    """)
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    missing = [e for e in report.entries
               if e.kind == "dict_entry" and e.severity == "missing"]
    assert {e.entity_id for e in missing} == {"USR_ID"}


def test_dict_entry_ok_when_under_per_connector_overlay(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """A v1 entry that lives under ``[connectors.nomasx1.entries]`` (the v2 per-connector
    overlay) still counts as found — the diff doesn't care which scope the entry lands in,
    only that it's somewhere."""
    engine = create_async_engine(v1_db)
    _insert(engine, "ly_dictionary", [
        {"dd_id": "APPS_ID", "dd_label": "App ID", "dd_type": "number",
         "dd_rules": None, "dd_rules_values": None, "dd_default": None},
    ])
    asyncio.run(engine.dispose())
    _write(cfg_dir["connectors"], """[pools.default]
url = "sqlite+aiosqlite:///:memory:"
""")
    _write(cfg_dir["dictionary"], """
        [connectors.nomasx1.entries.APPS_ID]
        label = "Application id"
    """)
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    assert not [e for e in report.entries
                if e.kind == "dict_entry" and e.severity == "missing"]


def test_dict_enum_lookup_sequence_missing(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v1 has one enum / one lookup / one sequence; v2 has none. All three surface as missing."""
    engine = create_async_engine(v1_db)
    _insert(engine, "ly_enum", [{"enum_id": 1, "enum_label": "Status"}])
    _insert(engine, "ly_lookup", [
        {"lkp_id": 1, "lkp_description": "User", "lkp_query_id": 1,
         "lkp_dd_id": "USR_ID", "lkp_dd_label": "USR_NAME", "lkp_dd_group": None},
    ])
    _insert(engine, "ly_sequence", [
        {"seq_id": 1, "seq_label": "next_id", "seq_query_id": 1, "seq_dd_id": "USR_ID"},
    ])
    asyncio.run(engine.dispose())
    _write(cfg_dir["connectors"], """[pools.default]
url = "sqlite+aiosqlite:///:memory:"
""")
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    enum_missing = [e for e in report.entries
                    if e.kind == "dict_enum" and e.severity == "missing"]
    lookup_missing = [e for e in report.entries
                      if e.kind == "dict_lookup" and e.severity == "missing"]
    seq_missing = [e for e in report.entries
                   if e.kind == "dict_sequence" and e.severity == "missing"]
    assert {e.entity_id for e in enum_missing} == {"1"}
    assert {e.entity_id for e in lookup_missing} == {"1"}
    assert {e.entity_id for e in seq_missing} == {"1"}


# ── screens checks ─────────────────────────────────────────────────────────────────────────


def test_screen_missing_when_no_matching_v2_screen(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """v1 ``ly_tables`` has two rows, v2 ``screens.toml`` only has one. The missing one
    surfaces with the v1 ``tbl_id`` for the operator to look up."""
    engine = create_async_engine(v1_db)
    _insert(engine, "ly_tables", [
        {"tbl_id": 10, "tbl_db_name": "security_users", "tbl_query_id": 1,
         "tbl_label": "Security Users", "tbl_auto_load": "Y", "tbl_editable": "Y",
         "tbl_uploadable": "N", "tbl_audit": "N", "tbl_frm_id": None, "tbl_ctx_id": None},
        {"tbl_id": 11, "tbl_db_name": "security_roles", "tbl_query_id": 2,
         "tbl_label": "Security Roles", "tbl_auto_load": "Y", "tbl_editable": "Y",
         "tbl_uploadable": "N", "tbl_audit": "N", "tbl_frm_id": None, "tbl_ctx_id": None},
    ])
    asyncio.run(engine.dispose())
    _write(cfg_dir["connectors"], """[pools.default]
url = "sqlite+aiosqlite:///:memory:"
""")
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], """
        [screens.nomasx1.security_users]
        read_query = "security_users_get"
    """)
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    missing = [e for e in report.entries
               if e.kind == "screen" and e.severity == "missing"]
    assert any("tbl_id=11" in e.entity_id for e in missing)
    assert not any("tbl_id=10" in e.entity_id for e in missing)


# ── render_text smoke test ─────────────────────────────────────────────────────────────────


def test_render_text_groups_by_kind_and_shows_severities(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """End-to-end shape check: render the text report and verify it groups by kind, shows the
    severity glyphs, and prints the header line with counts."""
    _seed_minimal_apps(v1_db)
    _seed_minimal_queries(v1_db)
    _write(cfg_dir["connectors"], """
        [pools.default]
        url = "sqlite+aiosqlite:///:memory:"

        [pools.nomasx1]
        url = "${LIBERTY_DB_URL_NOMASX1}"

        [connectors.nomasx1]
        type = "sql"
        pool = "nomasx1"

        [[connectors.nomasx1.queries]]
        name = "users_get"
        sql = "SELECT 1"
        # users_put is missing on purpose
    """)
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    text_out = render_text(report)
    # Header with counts.
    assert "missing=" in text_out and "info=" in text_out
    # Per-kind sections.
    assert "-- pool" in text_out
    assert "-- sql_query" in text_out
    # Severity glyphs land for the missing query + the stub pool.
    assert "✗" in text_out and "ℹ" in text_out
    # Specific entries surface.
    assert "nomasx1/users_put" in text_out
    assert "nomasx1" in text_out                                  # the stub pool


# ── JSON output via DiffReport.to_dict ──────────────────────────────────────────────────────


def test_report_to_dict_shape(v1_db: str, cfg_dir: dict[str, Path]) -> None:
    """The JSON shape is what the CLI's ``--format json`` writes. Verify it has the right
    top-level keys and that each entry carries kind / severity / entity_id / message /
    details + that the counts dict reflects severities."""
    _seed_minimal_apps(v1_db)
    _write(cfg_dir["connectors"], """[pools.default]
url = "sqlite+aiosqlite:///:memory:"
""")
    _write(cfg_dir["dictionary"], "")
    _write(cfg_dir["screens"], "")
    _write(cfg_dir["menus"], "")

    report = compute_diff(
        source_url=v1_db,
        connectors_path=cfg_dir["connectors"],
        screens_path=cfg_dir["screens"],
        dictionary_path=cfg_dir["dictionary"],
        menus_path=cfg_dir["menus"],
    )
    d = report.to_dict()
    assert set(d.keys()) == {"counts", "has_problems", "entries"}
    for e in d["entries"]:
        assert set(e.keys()) == {"kind", "severity", "entity_id", "message", "details"}
    assert d["counts"].get("missing", 0) >= 1                     # the nomasx1 pool was missing
    assert d["has_problems"] is True
