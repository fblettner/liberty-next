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
           apps_host, apps_port, apps_database, apps_pool_min, apps_pool_max, apps_limit
    FROM ly_applications ORDER BY apps_pool
""")
# Per-pool schema-name placeholders: `#SCHEMA.<sch_name>#` in a query → `sch_target` (e.g. SY → SY920).
_DB_SCHEMAS = text("SELECT sch_pool, sch_name, sch_target FROM ly_db_schema ORDER BY sch_pool, sch_name")
# Per-column display metadata: table-widget columns (ly_tbl_col ← ly_tables ← ly_query) and
# form-field columns (ly_dlg_col ← ly_dlg_frm ← ly_query). col_dd_id references a ly_dictionary
# entry (the migrated hint emits `dd = col_dd_id`); col_label/col_type are per-column overrides;
# col_key flags the row-identifying columns (→ the migrated `_put` WHERE binds them as :<col>_ORIGINAL).
_TBL_COLS = text("""
    SELECT t.tbl_query_id AS query_id, c.col_target, c.col_dd_id, c.col_label, c.col_seq,
           c.col_visible, c.col_type, c.col_filter, c.col_key, c.col_cdn_id, c.col_id
    FROM ly_tbl_col c JOIN ly_tables t ON t.tbl_id = c.tbl_id
    WHERE t.tbl_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
    ORDER BY t.tbl_query_id, c.tbl_id, c.col_seq, c.col_id
""")
# ly_dlg_col has no col_filter — alias NULL so the migration sees the same shape.
_DLG_COLS = text("""
    SELECT f.frm_query_id AS query_id, c.col_target, c.col_dd_id, c.col_label, c.col_seq,
           c.col_visible, c.col_type, NULL AS col_filter, c.col_key, c.col_cdn_id, c.col_id
    FROM ly_dlg_col c JOIN ly_dlg_frm f ON f.frm_id = c.frm_id
    WHERE f.frm_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
    ORDER BY f.frm_query_id, c.frm_id, c.col_seq, c.col_id
""")
# Conditional column rendering: a column's `col_cdn_id` (ly_tbl_col / ly_dlg_col) points at a
# condition in ly_cdn_params (a set of `cdn_dd_id <op> cdn_value` predicates). The migration distils
# the common shape (a field EQUAL one of a set of values, "+ EMPTY" = "or unset") into v2's
# ColumnHint.visible_when. (v1's ly_tbl_col_cdn / ly_dlg_col_cdn link tables aren't used — in the
# shipped apps they only carry redundant/no-op OR-branch mappings v2 can't represent anyway.)
_CDN_PARAMS = text(
    "SELECT cdn_id, cdn_dd_id, cdn_operator, cdn_value, cdn_group FROM ly_cdn_params "
    "WHERE cdn_dd_id IS NOT NULL ORDER BY cdn_id, cdn_seq"
)
# Cascading-filter rules: ly_tbl_filters (table widgets ← ly_tbl_col ← ly_tables) and
# ly_dlg_filters (form fields ← ly_dlg_col ← ly_dlg_frm). Each row says "for column <col_target>'s
# filter dropdown, when the <flt_source> filter has a value, narrow this dropdown's options to the
# lookup rows whose <flt_target> column matches it" (v1's flt_source / flt_target). `col` / `tgt`
# are aliased away from the reserved word `column`.
_TBL_FILTERS = text("""
    SELECT t.tbl_query_id AS query_id, c.col_target, f.flt_source AS src, f.flt_target AS tgt, f.flt_value AS val
    FROM ly_tbl_filters f
      JOIN ly_tbl_col c ON c.tbl_id = f.tbl_id AND c.col_id = f.col_id
      JOIN ly_tables t ON t.tbl_id = f.tbl_id
    WHERE t.tbl_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
      AND f.flt_source IS NOT NULL AND f.flt_target IS NOT NULL
    ORDER BY t.tbl_query_id, f.tbl_id, f.col_id, f.flt_id
""")
_DLG_FILTERS = text("""
    SELECT fr.frm_query_id AS query_id, c.col_target, f.flt_source AS src, f.flt_target AS tgt, f.flt_value AS val
    FROM ly_dlg_filters f
      JOIN ly_dlg_col c ON c.frm_id = f.frm_id AND c.col_id = f.col_id
      JOIN ly_dlg_frm fr ON fr.frm_id = f.frm_id
    WHERE fr.frm_query_id IS NOT NULL AND c.col_target IS NOT NULL AND c.col_target <> ''
      AND f.flt_source IS NOT NULL AND f.flt_target IS NOT NULL
    ORDER BY fr.frm_query_id, f.frm_id, f.col_id, f.flt_id
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
# Static parameter bindings per dictionary entry — v1's ly_dictionary_filters. Only the
# `flt_type='VALUE'` rows carry literal bindings; other rows (FIELD/DD/…) are dynamic and bind
# at form/table runtime — those are migrated separately (Phase 7 follow-up).
_DICTIONARY_FILTERS = text("""
    SELECT dd_id, flt_target, flt_value, flt_type
    FROM ly_dictionary_filters
    ORDER BY dd_id, flt_id
""")
# Declarative parameter names per lookup — v1's ly_lkp_params (one row per (lkp_id, dd_id)).
# These tell the builder which `:placeholder` names the lookup's query expects, so an entry
# that picks this lookup can auto-surface the bindable fields.
_LOOKUP_PARAMS = text("""
    SELECT lkp_id, dd_id
    FROM ly_lkp_params
    ORDER BY lkp_id, dd_id
""")
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
_TABLE_META = text(
    "SELECT tbl_query_id, tbl_label, tbl_auto_load, tbl_audit, tbl_db_name "
    "FROM ly_tables WHERE tbl_query_id IS NOT NULL ORDER BY tbl_id"
)
_DLG_FRM_META = text("SELECT frm_query_id, frm_label FROM ly_dlg_frm WHERE frm_query_id IS NOT NULL ORDER BY frm_id")
# Screens-side reads (slice 1 of phase 6). v1 has the chain:
#   ly_tables (the list/grid widget — links its dialog form via tbl_frm_id and its read
#     query via tbl_query_id; tbl_audit gates AUD_<table> writes)
#   ly_dialogs (a logical grouping; its label is what shows in the form's title)
#   ly_dlg_frm (the form — has its own frm_query_id, may differ from tbl_query_id)
#   ly_dlg_tab (the tabs — keyed by frm_id, ordered by tab_seq, cols = grid columns)
#   ly_dlg_col (the fields — keyed by (frm_id, col_id); tab_id picks the tab; col_target =
#     the result-column name, col_dd_id = the dictionary entry, plus flags col_visible /
#     col_disabled / col_required / col_default / col_key / col_seq / col_colspan)
#   ly_dlg_filters (parameter bindings for a field's lookup query — same shape as
#     ly_tbl_filters / ly_dictionary_filters: (flt_type=DD with flt_source) OR
#     (flt_type=VALUE with flt_value), flt_target = the lookup query's :param name)
_SCREENS_TABLES = text("""
    SELECT tbl_id, tbl_db_name, tbl_label, tbl_query_id, tbl_editable, tbl_uploadable,
           tbl_audit, tbl_auto_load, tbl_frm_id
    FROM ly_tables ORDER BY tbl_id
""")
_SCREENS_DIALOGS = text("SELECT dlg_id, dlg_label FROM ly_dialogs ORDER BY dlg_id")
_SCREENS_DLG_FRM = text("""
    SELECT frm_id, dlg_id, frm_label, frm_query_id
    FROM ly_dlg_frm ORDER BY frm_id
""")
_SCREENS_DLG_TAB = text("""
    SELECT frm_id, tab_id, tab_seq, tab_label, tab_cols,
           tab_disable_add, tab_disable_edit
    FROM ly_dlg_tab ORDER BY frm_id, COALESCE(tab_seq, tab_id)
""")
_SCREENS_DLG_TAB_L = text("""
    SELECT frm_id, tab_id, lng_id, lng_label
    FROM ly_dlg_tab_l ORDER BY frm_id, tab_id, lng_id
""")
_SCREENS_DLG_COL = text("""
    SELECT frm_id, col_id, tab_id, col_seq, col_colspan, col_dd_id, col_label, col_target,
           col_default, col_visible, col_disabled, col_required, col_cdn_id
    FROM ly_dlg_col ORDER BY frm_id, COALESCE(col_seq, col_id)
""")
_SCREENS_DLG_FILTERS = text("""
    SELECT frm_id, col_id, flt_id, flt_type, flt_source, flt_target, flt_value
    FROM ly_dlg_filters ORDER BY frm_id, col_id, flt_id
""")

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


async def read_db_schemas(engine: AsyncEngine) -> list[dict[str, Any]]:
    """Return ``ly_db_schema`` rows (``sch_pool``, ``sch_name``, ``sch_target``) — the per-pool
    ``#SCHEMA.<name>#`` placeholder map. Feeds :func:`liberty.migrations.v1.migrate_pools` (as
    ``db_schemas``). Missing table → ``[]``."""
    return await _rows_or_empty(engine, _DB_SCHEMAS, what="ly_db_schema")


async def read_column_hints(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_tbl_col`` rows, ``ly_dlg_col`` rows) — each joined to its query
    (via ``ly_tables`` / ``ly_dlg_frm``). Feeds :func:`liberty.migrations.v1.migrate_column_hints`.
    Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _TBL_COLS, what="ly_tbl_col → ly_tables column hints"),
        await _rows_or_empty(engine, _DLG_COLS, what="ly_dlg_col → ly_dlg_frm column hints"),
    )


async def read_column_conditions(engine: AsyncEngine) -> list[dict[str, Any]]:
    """Return the ``ly_cdn_params`` rows (the conditional-rendering predicates referenced by columns'
    ``col_cdn_id``). Feeds :func:`liberty.migrations.v1.migrate_column_visibility` (together with the
    ``ly_tbl_col`` / ``ly_dlg_col`` rows from :func:`read_column_hints`, which carry ``col_cdn_id`` /
    ``col_dd_id``). Missing table → ``[]``."""
    return await _rows_or_empty(engine, _CDN_PARAMS, what="ly_cdn_params (column-condition predicates)")


async def read_table_filters(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (``ly_tbl_filters`` rows, ``ly_dlg_filters`` rows) — cascading-filter rules joined to
    their column's ``col_target`` and their query id. Feeds
    :func:`liberty.migrations.v1.migrate_table_filters`. Missing tables → empty lists."""
    return (
        await _rows_or_empty(engine, _TBL_FILTERS, what="ly_tbl_filters → ly_tables cascading filters"),
        await _rows_or_empty(engine, _DLG_FILTERS, what="ly_dlg_filters → ly_dlg_frm cascading filters"),
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
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
]:
    """Return (``ly_enum`` rows, ``ly_enum_val`` rows, ``ly_enum_val_l`` rows, ``ly_lookup`` rows,
    ``ly_qry_sql⋈ly_query`` rows, ``ly_dictionary_filters`` rows, ``ly_lkp_params`` rows) — the
    v1 data the dictionary's display rules reference (``dd_rules = "ENUM"`` →
    ``ly_enum``/``ly_enum_val``; ``dd_rules = "LOOKUP"`` → ``ly_lookup``, whose ``lkp_query_id``
    resolves to a v2 query name through the joined sql rows; ``ly_dictionary_filters`` supplies
    static parameter bindings per dictionary entry; ``ly_lkp_params`` declares the param names
    each lookup's query expects). Missing tables → empty lists. Passed alongside
    :func:`read_dictionary` into :func:`liberty.migrations.v1.migrate_dictionary`."""
    return (
        await _rows_or_empty(engine, _ENUMS, what="ly_enum"),
        await _rows_or_empty(engine, _ENUM_VAL, what="ly_enum_val"),
        await _rows_or_empty(engine, _ENUM_VAL_L, what="ly_enum_val_l translations"),
        await _rows_or_empty(engine, _LOOKUPS, what="ly_lookup"),
        await _rows_or_empty(engine, _SQL_QUERIES, what="ly_qry_sql (lookup target → query name)"),
        await _rows_or_empty(engine, _DICTIONARY_FILTERS, what="ly_dictionary_filters (lookup static params)"),
        await _rows_or_empty(engine, _LOOKUP_PARAMS, what="ly_lkp_params (lookup param-name declarations)"),
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


async def read_screens(
    engine: AsyncEngine,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
]:
    """Return the seven row-sets v2's screens migration needs:

    1. ``ly_tables`` — the table widgets (with ``tbl_frm_id`` linking to the dialog, ``tbl_audit``
       for AUD, ``tbl_auto_load`` etc.)
    2. ``ly_dialogs`` — dialog containers (mostly an organisational layer; carries ``dlg_label``)
    3. ``ly_dlg_frm`` — dialog forms (the unit of "one form per screen")
    4. ``ly_dlg_tab`` — tabs within a form (one CSS grid of fields each)
    5. ``ly_dlg_tab_l`` — per-language tab labels (v1 had a separate `_l` table for tabs)
    6. ``ly_dlg_col`` — fields within a tab (col_target = the column name, col_dd_id = the dd ref)
    7. ``ly_dlg_filters`` — parameter bindings per field for the field's lookup query
    8. ``ly_qry_sql`` joined with ``ly_query`` — resolves ``tbl_query_id`` / ``frm_query_id`` to v2 query names

    Missing tables → empty lists (an old v1 schema without screens just produces no screens)."""
    return (
        await _rows_or_empty(engine, _SCREENS_TABLES, what="ly_tables (screens)"),
        await _rows_or_empty(engine, _SCREENS_DIALOGS, what="ly_dialogs"),
        await _rows_or_empty(engine, _SCREENS_DLG_FRM, what="ly_dlg_frm"),
        await _rows_or_empty(engine, _SCREENS_DLG_TAB, what="ly_dlg_tab"),
        await _rows_or_empty(engine, _SCREENS_DLG_TAB_L, what="ly_dlg_tab_l translations"),
        await _rows_or_empty(engine, _SCREENS_DLG_COL, what="ly_dlg_col"),
        await _rows_or_empty(engine, _SCREENS_DLG_FILTERS, what="ly_dlg_filters (field param binds)"),
        await _rows_or_empty(engine, _SQL_QUERIES, what="ly_qry_sql (screen query name resolution)"),
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
