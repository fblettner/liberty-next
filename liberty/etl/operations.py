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
    if_not_exists: bool = False,
) -> int:
    """``INSERT INTO history_table SELECT * FROM source_table [WHERE where]``
    on *target_connector*. Both tables live on the same connector (that's the
    v1 ``j_archive_data`` semantics — the history table is a sibling of the
    source on the target side, typically named ``<source>$`` by convention).

    ``where`` is a free SQL fragment with ``:name`` bind placeholders; pass
    values via ``params`` (the underlying driver handles quoting). Empty
    ``where`` snapshots every row.

    ``if_not_exists`` makes the INSERT idempotent — rows that would violate
    the history table's primary key are silently skipped instead of raising
    ``IntegrityError``. Used by the v1 SECURITY refresher chain
    (``snapshot → next_ukid → delete → copy``): if any step after the snapshot
    fails the row stays in history with its current ukid, and the next attempt
    would re-snapshot the same (apps_id, id, ukid) tuple. Without
    ``if_not_exists`` the operator has to manually clean ``<table>$`` rows
    before retrying — the kind of papercut that turns a 10-second retry into
    a 10-minute DBA detour. Dialect-aware: Postgres ``ON CONFLICT DO NOTHING``,
    SQLite ``INSERT OR IGNORE`` (the test fixture path); other dialects raise
    ``NotImplementedError`` so the caller doesn't silently get strict-mode
    behaviour they didn't ask for.

    Returns the number of rows inserted (driver-reported ``rowcount``; -1 on
    drivers that don't report it; with ``if_not_exists``, the count is "rows
    actually inserted", not "rows attempted" — duplicates aren't included).
    """
    engine = connectors.pools.engine(target_connector)
    where_clause = f" WHERE {where}" if where.strip() else ""
    if if_not_exists:
        dialect = engine.dialect.name
        if dialect == "postgresql":
            sql_text = (
                f"INSERT INTO {history_table} SELECT * FROM {source_table}"
                f"{where_clause} ON CONFLICT DO NOTHING"
            )
        elif dialect == "sqlite":
            # SQLite has no PostgreSQL-style ON CONFLICT after the SELECT — use
            # the statement-level ``INSERT OR IGNORE`` flavour, same semantic.
            sql_text = (
                f"INSERT OR IGNORE INTO {history_table} "
                f"SELECT * FROM {source_table}{where_clause}"
            )
        else:
            # Surface unknown dialects loudly — silently dropping ``if_not_exists``
            # would lock-step the caller back into the IntegrityError bug they
            # asked us to avoid. The current call sites are Postgres-only;
            # add an Oracle/MSSQL branch when a port needs one.
            raise NotImplementedError(
                f"snapshot_rows(if_not_exists=True) is not implemented for "
                f"dialect {dialect!r}; supported: 'postgresql', 'sqlite'"
            )
    else:
        sql_text = f"INSERT INTO {history_table} SELECT * FROM {source_table}{where_clause}"
    sql = text(sql_text)
    async with engine.begin() as conn:
        result = await conn.execute(sql, dict(params or {}))
    rows = result.rowcount or 0
    _log.info(
        "liberty.etl snapshot %s.%s → %s.%s where=%r rows=%d%s",
        target_connector, source_table, target_connector, history_table,
        where or "(all)", rows, " (if_not_exists)" if if_not_exists else "",
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
        _log.debug("liberty.etl truncate %s.%s (via DELETE; sqlite)", target_connector, table)
        return
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table}"))
    # DEBUG (not INFO): TRUNCATE is purely structural — operators don't need a
    # log line for every temp-table wipe inside the SECURITY_RIGHTS upsert
    # patterns. The business-level "what's happening" lives in the calling
    # nomasx1.security progress markers; this stays available at DEBUG when
    # someone needs to trace framework-level ops.
    _log.debug("liberty.etl truncate %s.%s", target_connector, table)


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


async def insert_audit_record(
    *,
    connectors: ConnectorRegistry,
    target_connector: str,
    target_schema: str | None,
    target_table: str,
    apps_id: int | str,
    module: str,
    action: str = "ETL",
    audit_table: str = "collect_audit",
    run_id: str | None = None,
) -> None:
    """Write one ``collect_audit`` row — the standard ETL audit log entry.

    Schema: ``(cla_apps_id, cla_module, cla_target, cla_action, cla_refresh,
    cla_run_id)``. Replaces v1's per-module audit tables (``SECURITY_AUDIT``,
    ``DB_AUDIT``, …) AND the earlier nomasx1b consolidated ``security_audit``
    (which lost the module categorization). One row per refresh event;
    cla_audit_id is a BIGSERIAL surrogate so the table is append-only.

    Arguments:
      * ``module`` — REQUIRED. Logical module ('SECURITY' | 'LICENSE' |
        'EMPLOYEES' | 'OUT' | 'SOD' | 'XREF' | 'DATABASE' | 'ACTIVITY_LOG'
        | 'AUDIT_TRAIL' | 'LDAP'). Restores v1's grouping — the dashboard
        widget answers 'when was LICENSE last refreshed?' from this column
        instead of having to infer from target names.
      * ``run_id`` — defaults to the active nomaflow run id pulled from
        :func:`liberty.jobs.runlog.current_run_id`. Passed-in value wins
        (e.g. ad-hoc backfill scripts that want to label their writes).
        Stored as ``cla_run_id``; nullable in the schema so writes outside
        a run context still succeed.
      * ``audit_table`` — table name, defaults to ``collect_audit``. Override
        only when an out-of-tree plugin wants its own audit log in the same
        schema (rare).
      * ``target_schema`` — when set, the table is schema-qualified
        (``<schema>.<audit_table>``).
    """
    # Auto-resolve the run id from the runlog ContextVar when the caller
    # didn't pass one. Imported lazily to keep liberty.etl independent of
    # liberty.jobs at import time (etl is the lower layer; jobs imports etl,
    # not the other way around — only this one helper bridges back).
    if run_id is None:
        from liberty.jobs.runlog import current_run_id
        run_id = current_run_id()
    engine = connectors.pools.engine(target_connector)
    qualified = f"{target_schema}.{audit_table}" if target_schema else audit_table
    sql = text(
        f"INSERT INTO {qualified} "
        f"(cla_apps_id, cla_module, cla_target, cla_action, cla_refresh, cla_run_id) "
        f"VALUES (:apps_id, :module, :target_table, :action, CURRENT_TIMESTAMP, :run_id)"
    )
    async with engine.begin() as conn:
        await conn.execute(
            sql,
            {
                "apps_id": apps_id,
                "module": module,
                "target_table": target_table,
                "action": action,
                "run_id": run_id,
            },
        )
    _log.info(
        "liberty.etl audit %s.%s ← (apps_id=%s, module=%s, target=%s, action=%s, run_id=%s)",
        target_connector, qualified, apps_id, module, target_table, action, run_id,
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
    # Two-line split:
    #   INFO: just rows=N (operator-level signal — "something happened" /
    #         "nothing happened"). Repeating the SQL on every per-role /
    #         per-user sub-call drowns the log in copies of the same template;
    #         the SQL doesn't change, the rowcount does.
    #   DEBUG: full SQL + rows so a developer / troubleshooter still sees
    #         exactly which statement ran. Flip via the per-run log_level
    #         setting (jobs.toml log_level = "DEBUG" or per-fire override).
    if rows > 0:
        _log.info("liberty.etl run_query %s rows=%d", connector, rows)
    else:
        _log.debug("liberty.etl run_query %s rows=0", connector)
    _log.debug("liberty.etl run_query %s rows=%d sql=%s", connector, rows, _short(sql))
    return rows


def _short(sql: str, *, limit: int = 120) -> str:
    """Collapse whitespace + truncate so log lines stay readable."""
    one_line = " ".join(sql.split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"
