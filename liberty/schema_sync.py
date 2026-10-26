"""Keep the framework's own tables in step with their models — the small cousin of Alembic.

The framework owns a handful of **state** tables on the default pool (nomaflow run history,
change packages, saved views, the optional ``ly2_*`` auth tables). They are created with
``create_all``, which adds missing *tables* and never touches an existing one: a release that
adds a column used to break every upgraded install until someone dropped the table (change
packages carried a hand-written ``ALTER TABLE`` list for exactly that). :func:`sync_tables`
generalises it from the models themselves:

1. ``create_all`` — new tables appear.
2. For every table that already exists, every model column missing from the live table is
   added with ``ALTER TABLE … ADD COLUMN``. The column type is rendered for the connection's
   dialect; a ``server_default`` or a scalar Python ``default`` becomes the ``DEFAULT`` so
   existing rows get a value; ``NOT NULL`` is emitted only when such a default exists (adding a
   NOT NULL column without one fails on a non-empty table) — otherwise the column is added
   nullable and the change carries a note.

Renames, type changes and drops are out of scope on purpose: nine tables that change a few
times a year do not justify a migrations tree, and the licensed apps already have Alembic
where it matters (``liberty-admin migrate``). What was added is returned so the caller can
journal it in the upgrade history (:func:`journal_schema_changes`) — ``liberty status`` and
the upgrade report then show framework schema changes next to the apps' migrations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Column, MetaData, Table, inspect, literal
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import ScalarElementColumnDefault

_log = logging.getLogger(__name__)

COMPONENT = "framework-schema"


@dataclass(frozen=True)
class SchemaChange:
    table: str
    column: str
    ddl: str
    note: str | None = None  # e.g. "model says NOT NULL but has no default — added nullable"

    def __str__(self) -> str:
        return f"{self.table}.{self.column}" + (f" ({self.note})" if self.note else "")


def _render_default(col: Column, conn: Connection) -> str | None:
    """The ``DEFAULT`` literal for *col*, or None when the model gives nothing usable
    (callables such as ``utcnow`` / ``uuid4`` can only run in Python)."""
    sd = col.server_default
    if sd is not None and getattr(sd, "arg", None) is not None:
        arg = sd.arg
        if isinstance(arg, str):
            return "'" + arg.replace("'", "''") + "'"
        return str(arg.compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
    d = col.default
    if isinstance(d, ScalarElementColumnDefault) and not d.is_callable:
        value = d.arg
        if isinstance(value, (str, int, float, bool)):
            return str(literal(value, type_=col.type).compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
    return None


def add_column_ddl(table: Table, col: Column, conn: Connection) -> tuple[str, str | None]:
    """``(ddl, note)`` for adding *col* to the live *table* on this connection's dialect."""
    q = conn.dialect.identifier_preparer.quote
    tname = q(table.name) if table.schema is None else f"{q(table.schema)}.{q(table.name)}"
    ctype = col.type.compile(dialect=conn.dialect)
    parts = [f"ALTER TABLE {tname} ADD COLUMN {q(col.name)} {ctype}"]
    default = _render_default(col, conn)
    note = None
    if default is not None:
        parts.append(f"DEFAULT {default}")
    if not col.nullable and not col.primary_key:
        if default is not None:
            parts.append("NOT NULL")
        else:
            note = "model says NOT NULL but has no default — added nullable"
    return " ".join(parts), note


def sync_tables(conn: Connection, metadata: MetaData) -> list[SchemaChange]:
    """``create_all`` + forward-add the columns the live tables lack. Sync API — call through
    ``await async_conn.run_sync(sync_tables, Base.metadata)``. Returns what was added (``[]``
    when everything was already in step)."""
    metadata.create_all(conn)
    insp = inspect(conn)
    changes: list[SchemaChange] = []
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name, schema=table.schema):
            continue
        live = {c["name"] for c in insp.get_columns(table.name, schema=table.schema)}
        for col in table.columns:
            if col.name in live:
                continue
            ddl, note = add_column_ddl(table, col, conn)
            conn.exec_driver_sql(ddl)
            change = SchemaChange(table.name, col.name, ddl, note)
            changes.append(change)
            _log.warning("schema_sync: added %s — %s", change, ddl)
    return changes


def journal_schema_changes(store: Any, changes: list[SchemaChange], *, who: str = "system") -> dict | None:
    """One ``framework-schema`` row in the upgrade history when *changes* is non-empty (what
    ``liberty status`` / the upgrade report show). *store* is a ``ConfigVersionStore``; a
    failure to journal is logged, never raised — the schema change itself already happened."""
    if not changes or store is None:
        return None
    try:
        from liberty import __version__

        return store.record_upgrade(
            component=COMPONENT, kind="upgrade",
            from_version=store.current_app_version(COMPONENT), to_version=__version__,
            summary="added " + ", ".join(str(c) for c in changes), who=who,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("schema_sync: could not journal %d change(s): %s", len(changes), exc)
        return None
