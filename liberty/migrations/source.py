"""Read the relevant ``ly_*`` rows from a v1 Liberty database.

v1 source is **read-only** — these only ``SELECT``. Pass any SQLAlchemy *async*
URL the deps can speak (``postgresql+asyncpg://…`` for a real v1 DB,
``sqlite+aiosqlite://…`` for a test fixture). The returned lists feed
:mod:`liberty.migrations.v1`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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


async def read_applications(engine: AsyncEngine) -> list[dict[str, Any]]:
    """Return ``ly_applications`` rows (one per v1 app/pool — connection details).

    Older v1 schemas may lack this table; treat that as "no pools to scaffold".
    """
    try:
        return await _rows(engine, _APPLICATIONS)
    except Exception:  # noqa: BLE001 — missing/renamed table on an old v1 → just skip pool scaffolding
        return []


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
