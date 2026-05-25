"""Source-to-target streaming copy — :func:`copy_query_to_table`.

The complement to :class:`liberty.jobs.SqlCopyExecutor` for the python-step
world: ``sql_copy`` is the *declarative* "copy this table verbatim" with JDE
coercion baked in; this is the *programmatic* "I have a custom SELECT, append
its rows to a target table" — used by nomasx1 module agents whose source SQL
is hand-written (filtered, joined, transformed) rather than ``SELECT *``.

Pattern:

* Opens **one** source connection + **one** target connection for the whole
  call (no per-batch reconnects — same anti-pattern guard as ``sql_copy``).
* ``source_engine.stream(...)`` partitions the result into batches; each
  batch is wrapped in a target-side transaction and INSERTed in one round
  trip (``conn.execute(insert, [row_dict, …])``).
* Column names are taken from the SELECT result, lowercased on the target
  side by convention (nomasx1 + nomajde Postgres tables are all lowercase).

There's no JDE-style coercion here — the caller's SQL is responsible for any
type fixes (Decimal → bigint, CHAR padding, etc.). If you need that, drive
the copy through a ``sql_copy`` step instead.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from sqlalchemy import text

from liberty.connectors import ConnectorRegistry

_log = logging.getLogger(__name__)


async def copy_query_to_table(
    *,
    connectors: ConnectorRegistry,
    source_connector: str,
    source_sql: str,
    source_params: Mapping[str, Any] | None = None,
    target_connector: str,
    target_table: str,
    target_columns: list[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """Run *source_sql* on *source_connector*, stream rows, INSERT them into
    *target_table* on *target_connector*. Returns rows inserted.

    *target_table* may be schema-qualified (``"nomasx1.SECURITY_USERS"``);
    *target_columns* defaults to the lowercased SELECT-result column names,
    which works for the nomasx1 convention (source uppercase, target
    lowercase). Pass an explicit list when target column names differ.

    Single transaction per batch keeps the WAL small and the locks short —
    same shape ``sql_copy._stream_into`` uses, just without the type
    coercion (this primitive is dialect-neutral; the caller picks the SQL).
    """
    src_engine = connectors.pools.engine(source_connector)
    tgt_engine = connectors.pools.engine(target_connector)
    select_sql = text(source_sql)
    bind = dict(source_params or {})

    rows_written = 0
    async with src_engine.connect() as src_conn, tgt_engine.connect() as tgt_conn:
        result = await src_conn.stream(select_sql, bind)
        insert_sql: Any | None = None  # built once we know the column names
        target_names: list[str] | None = None
        async for batch in result.partitions(batch_size):
            if not batch:
                continue
            if insert_sql is None:
                # Result columns are now known. Build the INSERT once. Source name
                # → target name: caller-supplied list wins; otherwise lowercase the
                # source names (nomasx1 / nomajde convention).
                source_names = list(batch[0]._mapping.keys())
                target_names = target_columns or [n.lower() for n in source_names]
                if len(target_names) != len(source_names):
                    raise ValueError(
                        f"target_columns length mismatch: source has "
                        f"{len(source_names)} columns, target_columns has "
                        f"{len(target_names)}"
                    )
                col_list = ", ".join(f'"{c}"' for c in target_names)
                bind_list = ", ".join(f":{c}" for c in target_names)
                insert_sql = text(f'INSERT INTO {target_table} ({col_list}) VALUES ({bind_list})')
            assert target_names is not None  # set on first batch above
            payload = [
                {tgt: row._mapping[src] for tgt, src in zip(target_names, batch[0]._mapping.keys())}
                for row in batch
            ]
            async with tgt_conn.begin():
                await tgt_conn.execute(insert_sql, payload)
            rows_written += len(payload)

    _log.info(
        "liberty.etl copy_query %s → %s.%s rows=%d batch_size=%d",
        source_connector, target_connector, target_table, rows_written, batch_size,
    )
    return rows_written
