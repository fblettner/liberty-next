"""Generic ETL primitives — the framework-level building blocks for python steps.

A ``python`` step's callable composes these into the customer-specific flow that
v1 had hardcoded as Spark/Airflow operators. The split is deliberate:

* **liberty/etl/** (here) — reusable, business-neutral. Things every ETL needs:
  snapshot a slice of rows into a history table, delete a slice, run an
  arbitrary query, append rows from a SELECT into a target table, write an
  audit record. No JDE / nomasx1 / customer specifics — those live in the
  apps repo's ``plugins/`` packages (PHASE13 §5.3) and import from here.

* **liberty-apps/plugins/<name>/** — proprietary. SQL templates, connection
  setup specific to a customer schema, module orchestration. Stays out of the
  open framework so the licensed bits aren't shipped with liberty-next.

All operations take a :class:`~liberty.connectors.ConnectorRegistry` and look
the target engine up by connector name (matches ``sql_copy``'s API shape), so
the same call works in dev (SQLite pool) and prod (Postgres pool) without
plumbing changes. Each function is async; sync callers can ``asyncio.run()``
or use the PythonStepExecutor's thread-pool fallback.

Identifier safety: table / column names passed to these helpers are *trusted*
inputs from agent code, not user input. They're interpolated into the SQL
unquoted, just like v1's f-string queries. ``where`` clauses are SQL fragments
with named ``:param`` binds — pass values through ``params``, never via string
formatting.
"""

from __future__ import annotations

from liberty.etl.copy import copy_query_to_table
from liberty.etl.operations import (
    delete_rows,
    insert_audit_record,
    run_query,
    snapshot_rows,
    truncate_table,
)

__all__ = [
    "copy_query_to_table",
    "delete_rows",
    "insert_audit_record",
    "run_query",
    "snapshot_rows",
    "truncate_table",
]
