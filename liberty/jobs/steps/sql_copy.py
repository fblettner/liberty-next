"""``sql_copy`` executor — stream rows from one SQL connector to another.

This is the Spark replacement (see ``docs/PHASE13.md`` §5.1). What it does:

1. Look up source + target as :class:`~liberty.connectors.SQLConnector` via
   the :class:`ConnectorRegistry`.
2. Introspect the source table's columns (SQLAlchemy ``Inspector``).
3. Apply :mod:`liberty.jobs.coercion` to derive a target DDL string.
4. Open **one** source connection + **one** target connection for the whole
   step (the spec explicitly calls out the per-batch-connection anti-pattern).
5. For ``overwrite`` mode:

   a. Create a ``__new`` work table on the target.
   b. Stream source → batches of coerced row dicts → INSERT into the work
      table (one transaction per batch — short transactions keep the WAL
      small and the locks brief).
   c. **Atomic swap** (one transaction): ``DROP IF EXISTS`` the live target
      and ``ALTER TABLE __new RENAME TO`` the live target. Production stays
      pointed at the previous run's data until the swap commits, so a
      mid-stream failure never leaves the target empty.

6. For ``append`` mode: skip the DDL + swap, INSERT into the target directly.
7. ``upsert`` is reserved (PHASE13 §11 #5) — raise :class:`StepFailed` with a
   message pointing at the alternative (a ``sql_query`` step on a hand-rolled
   MERGE/ON CONFLICT statement).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from liberty.connectors import ConnectorRegistry, UnknownConnectorError
from liberty.connectors.sql import SQLConnector
from liberty.jobs.coercion import (
    ColumnInfo,
    JDE_COERCION,
    build_target_ddl,
    coerce_row,
    target_column_names,
)
from liberty.jobs.schema import CopyMode, DecimalMode, Step, StepType, SqlEndpoint
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)


class SqlCopyExecutor:
    """Executes one ``sql_copy`` step. Stateless — same instance can run any
    number of steps concurrently, each gets its own pair of connections."""

    def __init__(self, connectors: ConnectorRegistry) -> None:
        self._connectors = connectors

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.SQL_COPY:
            raise StepFailed(
                f"SqlCopyExecutor received a step of type {step.type.value!r}"
            )
        src_conn, tgt_conn, mode = self._validate_endpoints(step)
        self._guard_self_copy(src_conn, step.source, tgt_conn, step.target)

        type_coercion = step.type_coercion or JDE_COERCION
        decimal_mode = step.decimal_mode or DecimalMode.TRUNCATE
        batch_size = step.batch_size

        src_engine = self._connectors.pools.engine(src_conn.pool_name)
        tgt_engine = self._connectors.pools.engine(tgt_conn.pool_name)

        _log.info(
            "nomaflow.sql_copy start run=%s step=%r %s.%s.%s → %s.%s.%s mode=%s coercion=%s decimal=%s",
            ctx.run_id, step.name,
            src_conn.name, step.source.schema_, step.source.table,  # type: ignore[union-attr]
            tgt_conn.name, step.target.schema_, step.target.table,  # type: ignore[union-attr]
            mode.value, type_coercion, decimal_mode.value,
        )

        # Step 1 — introspect source columns (one short connection use). Logged
        # before *and* after: if the source DB is unreachable this connect is
        # where the step hangs, and the "introspecting…" line with no following
        # "introspected" line pins the hang to the connection, not the copy.
        _log.info(
            "nomaflow.sql_copy run=%s step=%r introspecting source %s.%s (connector %s)",
            ctx.run_id, step.name, step.source.schema_, step.source.table, src_conn.name,  # type: ignore[union-attr]
        )
        columns = await _introspect_columns(
            src_engine, schema=step.source.schema_, table=step.source.table,  # type: ignore[union-attr]
        )
        _log.info(
            "nomaflow.sql_copy run=%s step=%r introspected %d column(s)",
            ctx.run_id, step.name, len(columns),
        )
        if not columns:
            raise StepFailed(
                f"sql_copy step {step.name!r}: source table "
                f"{step.source.schema_!r}.{step.source.table!r} has no columns "
                f"(table missing or empty schema)"
            )

        # Step 2 — execute the copy. Each branch opens its own target connection
        # so the connection's lifetime matches the work it's doing.
        if mode is CopyMode.OVERWRITE:
            rows_written = await self._do_overwrite(
                src_engine, tgt_engine, step, columns,
                type_coercion=type_coercion, decimal_mode=decimal_mode,
                batch_size=batch_size,
            )
        elif mode is CopyMode.APPEND:
            rows_written = await self._do_append(
                src_engine, tgt_engine, step, columns,
                type_coercion=type_coercion, decimal_mode=decimal_mode,
                batch_size=batch_size,
            )
        elif mode is CopyMode.UPSERT:
            # Per PHASE13 §11 #5: no clean cross-dialect implementation in
            # v1 of nomaflow. Operators who need this write a sql_query step
            # against a hand-rolled MERGE/ON CONFLICT statement.
            raise StepFailed(
                f"sql_copy step {step.name!r}: mode='upsert' is not implemented "
                "(use a sql_query step with a MERGE / ON CONFLICT statement instead)"
            )
        else:  # pragma: no cover — exhaustive
            raise StepFailed(f"sql_copy step {step.name!r}: unknown mode {mode!r}")

        _log.info(
            "nomaflow.sql_copy done run=%s step=%r rows=%d",
            ctx.run_id, step.name, rows_written,
        )
        return StepResult(rows_affected=rows_written)

    # -- validation ------------------------------------------------------ #

    def _validate_endpoints(self, step: Step) -> tuple[SQLConnector, SQLConnector, CopyMode]:
        if step.source is None or step.target is None:
            # The schema validator catches this, but a hand-built Step from a test could slip.
            raise StepFailed(
                f"sql_copy step {step.name!r} requires both source and target"
            )
        if not step.source.connector or not step.target.connector:
            raise StepFailed(
                f"sql_copy step {step.name!r}: source.connector and target.connector are required"
            )
        if not step.source.table or not step.target.table:
            raise StepFailed(
                f"sql_copy step {step.name!r}: source.table and target.table are required"
            )

        try:
            src = self._connectors.get(step.source.connector)
            tgt = self._connectors.get(step.target.connector)
        except UnknownConnectorError as exc:
            raise StepFailed(str(exc)) from exc

        if not isinstance(src, SQLConnector):
            raise StepFailed(
                f"sql_copy step {step.name!r}: source connector {step.source.connector!r} "
                f"is not a SQL connector (got {type(src).__name__})"
            )
        if not isinstance(tgt, SQLConnector):
            raise StepFailed(
                f"sql_copy step {step.name!r}: target connector {step.target.connector!r} "
                f"is not a SQL connector (got {type(tgt).__name__})"
            )
        mode = step.mode or CopyMode.OVERWRITE
        return src, tgt, mode

    @staticmethod
    def _guard_self_copy(
        src: SQLConnector, src_ep: SqlEndpoint | None,
        tgt: SQLConnector, tgt_ep: SqlEndpoint | None,
    ) -> None:
        """Refuse to copy a table onto itself — the overwrite path would DROP
        the source. Checking is cheap and catches a class of operator typos."""
        if src_ep is None or tgt_ep is None:
            return  # validated above; here for the typechecker
        if (src.pool_name == tgt.pool_name
                and src_ep.schema_ == tgt_ep.schema_
                and src_ep.table == tgt_ep.table):
            raise StepFailed(
                f"sql_copy refused: source and target resolve to the same "
                f"table on pool {src.pool_name!r} "
                f"({src_ep.schema_}.{src_ep.table})"
            )

    # -- overwrite path -------------------------------------------------- #

    async def _do_overwrite(
        self,
        src_engine: AsyncEngine,
        tgt_engine: AsyncEngine,
        step: Step,
        columns: list[ColumnInfo],
        *,
        type_coercion: str,
        decimal_mode: DecimalMode,
        batch_size: int,
    ) -> int:
        """Overwrite a target table without ever leaving production empty.

        Pattern (stash + rename — works on every dialect, with or without
        transactional DDL):

        1. Build ``<table>__new`` from scratch + stream rows into it.
        2. If ``<table>`` exists, rename it to ``<table>__old``.
        3. Rename ``<table>__new`` → ``<table>``.
        4. On success: drop ``<table>__old``.
        5. If step 3 fails after step 2: try to rename ``<table>__old`` →
           ``<table>`` to restore the previous run's data.

        Why not "wrap DROP + RENAME in one transaction": SQLite (via aiosqlite)
        does NOT roll back ``DROP TABLE`` inside an explicit transaction — the
        DROP commits even when the surrounding ``async with conn.begin()``
        exits with an exception. Postgres has transactional DDL; Oracle is
        partial; SQLite doesn't. Stash + rename works on all three without
        relying on the driver's quirks.

        The visibility window where ``<table>`` doesn't exist (between steps 2
        and 3) is microseconds — two ALTER TABLE statements back to back.
        Postgres concurrent readers queue on AccessExclusiveLock; SQLite
        readers see "no such table" briefly if they hit the gap.
        """
        assert step.target is not None and step.target.table is not None  # validated
        tgt_schema = step.target.schema_
        live_table = step.target.table
        work_table = f"{live_table}__new"
        stash_table = f"{live_table}__old"

        target_names = target_column_names(columns, type_coercion=type_coercion)
        ddl = build_target_ddl(
            tgt_schema, work_table, columns,
            type_coercion=type_coercion, decimal_mode=decimal_mode,
        )

        async with tgt_engine.connect() as tgt_conn:
            # Clear any leftovers from a prior failed run, then create the work table.
            await tgt_conn.execute(text(_drop_if_exists_sql(tgt_schema, work_table)))
            await tgt_conn.execute(text(_drop_if_exists_sql(tgt_schema, stash_table)))
            await tgt_conn.execute(text(ddl))
            await tgt_conn.commit()

            rows_written = await self._stream_into(
                src_engine, tgt_conn, step, columns,
                target_schema=tgt_schema, target_table=work_table,
                target_names=target_names,
                type_coercion=type_coercion, decimal_mode=decimal_mode,
                batch_size=batch_size,
            )

            # The swap. Each rename is its own statement; rollback semantics
            # rely on the recovery rename in the except block, not on a DDL
            # transaction.
            live_existed = await _table_exists(tgt_conn, tgt_schema, live_table)
            if live_existed:
                await tgt_conn.execute(text(_rename_sql(tgt_schema, live_table, stash_table)))
                await tgt_conn.commit()
            try:
                await tgt_conn.execute(text(_rename_sql(tgt_schema, work_table, live_table)))
                await tgt_conn.commit()
            except Exception:
                if live_existed:
                    # Best-effort recovery: put the previous live table back.
                    try:
                        await tgt_conn.execute(text(_rename_sql(tgt_schema, stash_table, live_table)))
                        await tgt_conn.commit()
                    except Exception:  # pragma: no cover
                        _log.exception(
                            "nomaflow.sql_copy recovery rename failed for %s; "
                            "stash table %s remains for manual recovery",
                            live_table, stash_table,
                        )
                raise
            # Successful swap — drop the stash.
            if live_existed:
                await tgt_conn.execute(text(_drop_if_exists_sql(tgt_schema, stash_table)))
                await tgt_conn.commit()

        return rows_written

    # -- append path ----------------------------------------------------- #

    async def _do_append(
        self,
        src_engine: AsyncEngine,
        tgt_engine: AsyncEngine,
        step: Step,
        columns: list[ColumnInfo],
        *,
        type_coercion: str,
        decimal_mode: DecimalMode,
        batch_size: int,
    ) -> int:
        assert step.target is not None and step.target.table is not None  # validated
        target_names = target_column_names(columns, type_coercion=type_coercion)
        async with tgt_engine.connect() as tgt_conn:
            return await self._stream_into(
                src_engine, tgt_conn, step, columns,
                target_schema=step.target.schema_, target_table=step.target.table,
                target_names=target_names,
                type_coercion=type_coercion, decimal_mode=decimal_mode,
                batch_size=batch_size,
            )

    # -- shared streaming path ------------------------------------------ #

    async def _stream_into(
        self,
        src_engine: AsyncEngine,
        tgt_conn: AsyncConnection,
        step: Step,
        columns: list[ColumnInfo],
        *,
        target_schema: str | None,
        target_table: str,
        target_names: list[str],
        type_coercion: str,
        decimal_mode: DecimalMode,
        batch_size: int,
    ) -> int:
        assert step.source is not None and step.source.table is not None
        insert_sql = text(_insert_sql(target_schema, target_table, target_names))
        select_sql = text(_select_all_sql(step.source.schema_, step.source.table))

        rows_written = 0
        async with src_engine.connect() as src_conn:
            result = await src_conn.stream(select_sql)
            async for batch in result.partitions(batch_size):
                if not batch:
                    continue
                transformed = [
                    coerce_row(
                        dict(row._mapping), columns,
                        type_coercion=type_coercion, decimal_mode=decimal_mode,
                    )
                    for row in batch
                ]
                async with tgt_conn.begin():
                    await tgt_conn.execute(insert_sql, transformed)
                rows_written += len(transformed)
        return rows_written


# --------------------------------------------------------------------------- #
# small SQL string helpers — kept module-level so they're easy to unit-test if
# we want to (today the coverage comes via the integration tests)
# --------------------------------------------------------------------------- #


def _qualified(schema: str | None, table: str) -> str:
    return f'"{schema}"."{table}"' if schema else f'"{table}"'


def _drop_if_exists_sql(schema: str | None, table: str) -> str:
    return f"DROP TABLE IF EXISTS {_qualified(schema, table)}"


def _rename_sql(schema: str | None, from_name: str, to_name: str) -> str:
    # NB: the *to* name in ALTER TABLE ... RENAME TO is **unqualified** on
    # Postgres / SQLite / Oracle — the table stays in its current schema by
    # definition. Qualifying the new name with the same schema is a syntax
    # error on Postgres; leaving it unqualified is the cross-dialect form.
    return f'ALTER TABLE {_qualified(schema, from_name)} RENAME TO "{to_name}"'


def _insert_sql(schema: str | None, table: str, columns: list[str]) -> str:
    if not columns:
        raise ValueError("INSERT needs at least one column")
    col_list = ", ".join(f'"{n}"' for n in columns)
    param_list = ", ".join(f":{n}" for n in columns)
    return f"INSERT INTO {_qualified(schema, table)} ({col_list}) VALUES ({param_list})"


def _select_all_sql(schema: str | None, table: str) -> str:
    return f"SELECT * FROM {_qualified(schema, table)}"


# --------------------------------------------------------------------------- #
# schema introspection
# --------------------------------------------------------------------------- #


async def _table_exists(
    conn: AsyncConnection, schema: str | None, table: str,
) -> bool:
    """Cheap "does the target table exist right now" check — drives the
    stash-rename branch in :meth:`SqlCopyExecutor._do_overwrite`.

    Uses the synchronous Inspector via ``conn.run_sync``; the result is from
    the live connection so it sees any uncommitted DDL the current connection
    has done (though our overwrite path commits between work-table creation
    and the existence check, so this is just defensive).
    """
    from sqlalchemy import inspect as sa_inspect

    def _check(sync_conn: Any) -> bool:
        return sa_inspect(sync_conn).has_table(table, schema=schema)
    return await conn.run_sync(_check)


async def _introspect_columns(
    engine: AsyncEngine, *, schema: str | None, table: str,
) -> list[ColumnInfo]:
    """Return the source table's columns as :class:`ColumnInfo`.

    Uses ``conn.run_sync`` to call the synchronous SQLAlchemy ``Inspector``
    — the async engines (asyncpg, oracledb, aiosqlite) don't have a native
    async reflection API; ``run_sync`` is the documented hop.

    Dialects differ on what they do for a missing table: Postgres returns an
    empty list, SQLite raises :class:`NoSuchTableError`. Normalise to "empty
    list" — the caller treats an empty column list as :class:`StepFailed`
    with a clear message naming the missing table."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.exc import NoSuchTableError

    async with engine.connect() as conn:
        def _do_inspect(sync_conn: Any) -> list[dict[str, Any]]:
            inspector = sa_inspect(sync_conn)
            try:
                return inspector.get_columns(table, schema=schema)
            except NoSuchTableError:
                return []
        col_dicts = await conn.run_sync(_do_inspect)

    return [
        ColumnInfo(
            name=str(c["name"]),
            type=c["type"],
            nullable=bool(c.get("nullable", True)),
        )
        for c in col_dicts
    ]
