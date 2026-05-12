"""Read the relevant ``ly_*`` rows from a v1 Liberty database.

v1 source is **read-only** — these only ``SELECT``. Pass any SQLAlchemy *async*
URL the deps can speak (``postgresql+asyncpg://…`` for a real v1 DB,
``sqlite+aiosqlite://…`` for a test fixture). The returned lists feed
:mod:`liberty.migrations.v1`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_log = logging.getLogger("liberty.migrations")

# Only the columns the migration uses — keeps it resilient to v1 schema drift.
_SQL_QUERIES = text("""
    SELECT q.query_id, q.query_label, q.query_type,
           s.query_dbtype, s.query_crud, s.query_pool, s.query_sqlquery, s.query_orderby
    FROM ly_qry_sql s JOIN ly_query q ON q.query_id = s.query_id
    ORDER BY s.query_pool, q.query_id, s.query_crud, s.query_dbtype
""")
_QUERIES = text("SELECT query_id, query_label, query_type FROM ly_query ORDER BY query_id")
_APPLICATIONS = text("""
    SELECT apps_name, apps_pool, apps_dbtype, apps_jdbc, apps_user, apps_password,
           apps_host, apps_port, apps_database, apps_pool_min, apps_pool_max
    FROM ly_applications ORDER BY apps_pool
""")
# Per-column display metadata: table-widget columns (ly_tbl_col ← ly_tables ← ly_query) and
# form-field columns (ly_dlg_col ← ly_dlg_frm ← ly_query). col_dd_id references a ly_dictionary
# entry (the migrated hint emits `dd = col_dd_id`); col_label/col_type are per-column overrides.
_TBL_COLS = text("""
    SELECT t.tbl_query_id AS query_id, c.col_target, c.col_dd_id, c.col_label, c.col_seq,
           c.col_visible, c.col_type, c.col_id
    FROM ly_tbl_col c JOIN ly_tables t ON t.tbl_id = c.tbl_id
    WHERE t.tbl_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
    ORDER BY t.tbl_query_id, c.tbl_id, c.col_seq, c.col_id
""")
_DLG_COLS = text("""
    SELECT f.frm_query_id AS query_id, c.col_target, c.col_dd_id, c.col_label, c.col_seq,
           c.col_visible, c.col_type, c.col_id
    FROM ly_dlg_col c JOIN ly_dlg_frm f ON f.frm_id = c.frm_id
    WHERE f.frm_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
    ORDER BY f.frm_query_id, c.frm_id, c.col_seq, c.col_id
""")
# The shared field dictionary (ly_dictionary) + its per-language labels (ly_dictionary_l).
_DICTIONARY = text("""
    SELECT dd_id, dd_label, dd_type, dd_rules, dd_rules_values, dd_default
    FROM ly_dictionary ORDER BY dd_id
""")
_DICTIONARY_L = text("SELECT dd_id, lng_id, lng_label FROM ly_dictionary_l ORDER BY dd_id, lng_id")
# Display rules that the dictionary entries reference via dd_rules / dd_rules_values:
# ly_enum + ly_enum_val (+ ly_enum_val_l translations) feed `[enums.*]`; ly_lookup feeds
# `[lookups.*]` (its lkp_query_id resolves to a v2 query name through ly_qry_sql).
_ENUMS = text("SELECT enum_id, enum_label FROM ly_enum ORDER BY enum_id")
_ENUM_VAL = text("SELECT enum_id, val_enum, val_label FROM ly_enum_val ORDER BY enum_id, val_enum")
_ENUM_VAL_L = text(
    "SELECT enum_id, val_enum, lng_id, lng_label FROM ly_enum_val_l ORDER BY enum_id, val_enum, lng_id"
)
_LOOKUPS = text(
    "SELECT lkp_id, lkp_description, lkp_query_id, lkp_dd_id, lkp_dd_label, lkp_dd_group "
    "FROM ly_lookup ORDER BY lkp_id"
)
# App navigation: ly_menus (the tree, by menu_seq_ukid) + ly_menus_l (per-language labels) +
# the lookup tables that resolve a node's menu_component/menu_component_id to a query
# (ly_tables.tbl_id → tbl_query_id ; ly_dlg_frm.frm_id → frm_query_id ; ly_query → label).
_MENUS = text("""
    SELECT menu_seq_ukid, menu_parent_id, menu_child_id, menu_component, menu_component_id, menu_label, menu_level
    FROM ly_menus ORDER BY menu_seq_ukid
""")
_MENUS_L = text("SELECT lng_id, lng_seq_ukid, lng_label FROM ly_menus_l ORDER BY lng_seq_ukid, lng_id")
_TABLES = text("SELECT tbl_id, tbl_query_id FROM ly_tables WHERE tbl_query_id IS NOT NULL ORDER BY tbl_id")
_DLG_FRM = text("SELECT frm_id, frm_query_id FROM ly_dlg_frm WHERE frm_query_id IS NOT NULL ORDER BY frm_id")
# table/form *display* metadata: friendly label → v2 query.description ; auto-load flag → query.auto_load
_TABLE_META = text("SELECT tbl_query_id, tbl_label, tbl_auto_load FROM ly_tables WHERE tbl_query_id IS NOT NULL ORDER BY tbl_id")
_DLG_FRM_META = text("SELECT frm_query_id, frm_label FROM ly_dlg_frm WHERE frm_query_id IS NOT NULL ORDER BY frm_id")
_API_CONNS = text("SELECT conn_id, conn_label, conn_url, conn_user, conn_password FROM ly_api_conn ORDER BY conn_id")
_APIS = text("SELECT api_id, api_label, api_source, api_method, api_url, api_user, api_password, api_body, api_conn_id FROM ly_api ORDER BY api_id")
_API_HEADERS = text("SELECT api_id, hdr_id, hdr_key, hdr_value FROM ly_api_header ORDER BY api_id, hdr_id")
_API_PARAMS = text("SELECT api_id, map_id, map_var, map_value FROM ly_api_params ORDER BY api_id, map_id")


def make_engine(source_url: str) -> AsyncEngine:
    return create_async_engine(source_url)


async def _rows(engine: AsyncEngine, stmt) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [dict(r) for r in result.mappings()]


async def read_sql_queries(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_query`` rows, ``ly_qry_sql`` rows joined with their labels)."""
    return await _rows(engine, _QUERIES), await _rows(engine, _SQL_QUERIES)


async def _rows_or_empty(engine: AsyncEngine, stmt, *, what: str) -> list[dict[str, Any]]:
    """Like :func:`_rows`, but a missing/renamed table on an old v1 schema → ``[]`` (logged,
    not silently swallowed — so a real schema mismatch is at least visible)."""
    try:
        return await _rows(engine, stmt)
    except Exception as exc:  # noqa: BLE001 — best-effort: an absent table just means "nothing here"
        _log.warning("migration: skipped %s — %s: %s", what, type(exc).__name__, exc)
        return []


async def read_applications(engine: AsyncEngine) -> list[dict[str, Any]]:
    """Return ``ly_applications`` rows (one per v1 app/pool — connection details).
    Older v1 schemas may lack this table; treat that as "no pools to scaffold"."""
    return await _rows_or_empty(engine, _APPLICATIONS, what="ly_applications")


async def read_column_hints(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_tbl_col`` rows, ``ly_dlg_col`` rows) — each joined to its query
    (via ``ly_tables`` / ``ly_dlg_frm``). Feeds :func:`liberty.migrations.v1.migrate_column_hints`.
    Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _TBL_COLS, what="ly_tbl_col → ly_tables column hints"),
        await _rows_or_empty(engine, _DLG_COLS, what="ly_dlg_col → ly_dlg_frm column hints"),
    )


async def read_table_meta(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_tables`` rows, ``ly_dlg_frm`` rows) carrying display metadata — the table/form
    friendly label and ``tbl_auto_load`` flag, keyed by query id. Feeds
    :func:`liberty.migrations.v1.migrate_table_meta`. Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _TABLE_META, what="ly_tables (label / auto-load)"),
        await _rows_or_empty(engine, _DLG_FRM_META, what="ly_dlg_frm (label)"),
    )


async def read_dictionary(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_dictionary`` rows, ``ly_dictionary_l`` rows) — the shared field dictionary
    and its per-language labels. Feeds :func:`liberty.migrations.v1.migrate_dictionary`.
    Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _DICTIONARY, what="ly_dictionary"),
        await _rows_or_empty(engine, _DICTIONARY_L, what="ly_dictionary_l translations"),
    )


async def read_dictionary_rules(
    engine: AsyncEngine,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_enum`` rows, ``ly_enum_val`` rows, ``ly_enum_val_l`` rows, ``ly_lookup`` rows,
    ``ly_qry_sql⋈ly_query`` rows) — the v1 data the dictionary's display rules reference
    (``dd_rules = "ENUM"`` → ``ly_enum``/``ly_enum_val``; ``dd_rules = "LOOKUP"`` → ``ly_lookup``,
    whose ``lkp_query_id`` resolves to a v2 query name through the joined sql rows). Missing
    tables → empty lists. Passed alongside :func:`read_dictionary` into
    :func:`liberty.migrations.v1.migrate_dictionary`."""
    return (
        await _rows_or_empty(engine, _ENUMS, what="ly_enum"),
        await _rows_or_empty(engine, _ENUM_VAL, what="ly_enum_val"),
        await _rows_or_empty(engine, _ENUM_VAL_L, what="ly_enum_val_l translations"),
        await _rows_or_empty(engine, _LOOKUPS, what="ly_lookup"),
        await _rows_or_empty(engine, _SQL_QUERIES, what="ly_qry_sql (lookup target → query name)"),
    )


async def read_menus(
    engine: AsyncEngine,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_menus`` rows, ``ly_menus_l`` rows, ``ly_tables`` rows, ``ly_dlg_frm`` rows,
    ``ly_qry_sql`` rows joined with ``ly_query``) — the menu tree, its translations, and the
    lookup tables that resolve a node's ``menu_component``/``menu_component_id`` to a v2 query
    name (the joined query rows give both the label and the read CRUD that name the migrated
    query). Feeds :func:`liberty.migrations.v1.migrate_menus`. Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _MENUS, what="ly_menus"),
        await _rows_or_empty(engine, _MENUS_L, what="ly_menus_l translations"),
        await _rows_or_empty(engine, _TABLES, what="ly_tables (menu component → query)"),
        await _rows_or_empty(engine, _DLG_FRM, what="ly_dlg_frm (menu component → query)"),
        await _rows_or_empty(engine, _SQL_QUERIES, what="ly_qry_sql (menu component → query name)"),
    )


async def read_api(
    engine: AsyncEngine,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_api_conn``, ``ly_api``, ``ly_api_header``, ``ly_api_params``) rows."""
    return (
        await _rows(engine, _API_CONNS),
        await _rows(engine, _APIS),
        await _rows(engine, _API_HEADERS),
        await _rows(engine, _API_PARAMS),
    )
