"""Single-statement ETL operations — snapshot, delete, truncate, audit, run_query.

Each helper resolves the engine via the :class:`ConnectorRegistry` and runs
exactly one SQL statement inside an ``engine.begin()`` block (one transaction
per call). They return either an inserted/deleted row count (``int``) or
nothing — see per-function docs.

The streaming source-to-target case (a SELECT on one engine + an INSERT on
another, with batched coercion) is :func:`liberty.etl.copy_query_to_table`,
in its own module because the row-loop logic is different shape.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from sqlalchemy import text

from liberty.connectors import ConnectorRegistry

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# snapshot — copy a slice of rows into a history table
# --------------------------------------------------------------------------- #


async def snapshot_rows(
    *,
    connectors: ConnectorRegistry,
    target_connector: str,
    source_table: str,
    history_table: str,
    where: str = "",
    params: Mapping[str, Any] | None = None,
) -> int:
    """``INSERT INTO history_table SELECT * FROM source_table [WHERE where]``
    on *target_connector*. Both tables live on the same connector (that's the
    v1 ``j_archive_data`` semantics — the history table is a sibling of the
    source on the target side, typically named ``<source>$`` by convention).

    ``where`` is a free SQL fragment with ``:name`` bind placeholders; pass
    values via ``params`` (the underlying driver handles quoting). Empty
    ``where`` snapshots every row.

    Returns the number of rows inserted (driver-reported ``rowcount``; -1 on
    drivers that don't report it).
    """
    engine = connectors.pools.engine(target_connector)
    where_clause = f" WHERE {where}" if where.strip() else ""
    sql = text(f"INSERT INTO {history_table} SELECT * FROM {source_table}{where_clause}")
    async with engine.begin() as conn:
        result = await conn.execute(sql, dict(params or {}))
    rows = result.rowcount or 0
    _log.info(
        "liberty.etl snapshot %s.%s → %s.%s where=%r rows=%d",
        target_connector, source_table, target_connector, history_table, where or "(all)", rows,
    )
    return rows


# --------------------------------------------------------------------------- #
# delete + truncate
# --------------------------------------------------------------------------- #


async def delete_rows(
    *,
    connectors: ConnectorRegistry,
    target_connector: str,
    table: str,
    where: str = "",
    params: Mapping[str, Any] | None = None,
) -> int:
    """``DELETE FROM table [WHERE where]`` on *target_connector*. Empty
    ``where`` deletes every row (most drivers do this without a TABLE-level
    lock — use :func:`truncate_table` when that matters).

    Returns the number of rows deleted (driver-reported).
    """
    engine = connectors.pools.engine(target_connector)
    where_clause = f" WHERE {where}" if where.strip() else ""
    sql = text(f"DELETE FROM {table}{where_clause}")
    async with engine.begin() as conn:
        result = await conn.execute(sql, dict(params or {}))
    rows = result.rowcount or 0
    _log.info(
        "liberty.etl delete %s.%s where=%r rows=%d",
        target_connector, table, where or "(all)", rows,
    )
    return rows


async def truncate_table(
    *,
    connectors: ConnectorRegistry,
    target_connector: str,
    table: str,
) -> None:
    """``TRUNCATE TABLE table`` on *target_connector* — fast wipe (no
    per-row WAL, takes an ACCESS EXCLUSIVE lock on Postgres). Returns
    nothing; TRUNCATE has no row count.

    SQLite doesn't support TRUNCATE; the helper falls back to ``DELETE FROM``
    so test fixtures (which use SQLite) behave the same.
    """
    engine = connectors.pools.engine(target_connector)
    # SQLite: TRUNCATE is a syntax error. Detect via the engine's dialect.
    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM {table}"))
        _log.info("liberty.etl truncate %s.%s (via DELETE; sqlite)", target_connector, table)
        return
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table}"))
    _log.info("liberty.etl truncate %s.%s", target_connector, table)


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


async def insert_audit_record(
    *,
    connectors: ConnectorRegistry,
    target_connector: str,
    audit_table: str,
    target_schema: str | None,
    target_table: str,
    apps_id: int | str,
    action: str = "ETL",
) -> None:
    """Write a standard audit row — ``(apps_id, target_table, action, CURRENT_DATE)``.

    Matches v1's ``j_insert_audit`` shape (every ETL step writes one of these
    to its module-specific audit table — ``SECURITY_AUDIT``, ``DB_AUDIT``, …).
    Schema-qualified when *target_schema* is given.
    """
    engine = connectors.pools.engine(target_connector)
    qualified = f"{target_schema}.{audit_table}" if target_schema else audit_table
    sql = text(
        f"INSERT INTO {qualified} VALUES (:apps_id, :target_table, :action, CURRENT_DATE)"
    )
    async with engine.begin() as conn:
        await conn.execute(sql, {"apps_id": apps_id, "target_table": target_table, "action": action})
    _log.info(
        "liberty.etl audit %s.%s ← (%s, %s, %s, CURRENT_DATE)",
        target_connector, qualified, apps_id, target_table, action,
    )


# --------------------------------------------------------------------------- #
# run_query — escape hatch for one-off DDL / DML
# --------------------------------------------------------------------------- #


async def run_query(
    *,
    connectors: ConnectorRegistry,
    connector: str,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> int:
    """Execute *sql* on *connector* and return ``rowcount`` (or 0 for DDL).

    The escape hatch — use this when a port needs a one-off statement that
    doesn't fit the typed helpers (a REFRESH MATERIALIZED VIEW, an ad-hoc
    UPDATE, a procedure call). For repeating patterns, add a dedicated
    helper instead so the call site stays declarative.
    """
    engine = connectors.pools.engine(connector)
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), dict(params or {}))
    rows = result.rowcount or 0
    _log.info("liberty.etl run_query %s rows=%d sql=%s", connector, rows, _short(sql))
    return rows


def _short(sql: str, *, limit: int = 120) -> str:
    """Collapse whitespace + truncate so log lines stay readable."""
    one_line = " ".join(sql.split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"
