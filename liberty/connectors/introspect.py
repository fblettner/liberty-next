"""Pool introspection — list a pool's tables/views + their columns at runtime.

Used by the Phase-7 SQL editor in the config builder: the Monaco autocomplete
suggests table names after ``FROM`` / ``JOIN`` / ``UPDATE`` / ``INSERT INTO`` and
column names after a ``<table>.``; the wizard's table picker draws from the same
list. The catalog is read lazily on demand and cached per-engine; refresh the
pool to re-read.

Implementation: :func:`sqlalchemy.inspect` over an async engine via
``connection.run_sync()`` — works across PostgreSQL / Oracle / MSSQL / SQLite
without us writing per-dialect SQL. We list the *non-system* schemas, scoped to
the pool's ``schemas`` map when set (so an Oracle pool whose ``[pools.X.schemas]``
points at ``SY920`` doesn't dump every owner in ``ALL_TAB_COLUMNS``), else the
inspector's default (which is the connection's current schema for most dialects).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Inspector, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from liberty.connectors.db import PoolRegistry

_log = logging.getLogger(__name__)

# Schemas the inspector should never enumerate — internal catalog/system stuff
# that doesn't belong in an autocomplete list. (Per dialect; merged at lookup.)
_HIDDEN_SCHEMAS: dict[str, set[str]] = {
    "postgresql": {"pg_catalog", "information_schema", "pg_toast"},
    "oracle": {"SYS", "SYSTEM", "XDB", "MDSYS", "CTXSYS", "OUTLN", "WMSYS", "EXFSYS", "OLAPSYS"},
    "mssql": {"sys", "INFORMATION_SCHEMA"},
}
# Hard ceiling — if a pool exposes more tables than this we truncate (with a flag).
# Anything above ~5K is going to overwhelm an autocomplete list anyway.
DEFAULT_MAX_TABLES = 2000


@dataclass(slots=True)
class ColumnInfo:
    name: str
    type: str | None = None
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.type is not None:
            d["type"] = self.type
        if not self.nullable:
            d["nullable"] = False
        return d


@dataclass(slots=True)
class TableInfo:
    name: str
    schema: str | None = None       # spelled out only when the pool has multiple schemas
    kind: str = "table"             # "table" / "view"
    columns: list[ColumnInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "kind": self.kind, "columns": [c.to_dict() for c in self.columns]}
        if self.schema is not None:
            d["schema"] = self.schema
        return d


def _scoped_schemas(inspector: Inspector, dialect: str, pool_schemas: dict[str, str]) -> list[str | None]:
    """Pick which schemas to enumerate. When the pool's TOML defines a ``schemas`` map
    (the `#SCHEMA.<NAME>#` substitutions), prefer those — they're the ones the operator
    expects the builder's autocomplete to cover. Otherwise list every non-hidden schema
    the inspector reports; an empty list collapses to ``[None]`` (use the connection's
    default schema)."""
    if pool_schemas:
        return sorted({v for v in pool_schemas.values() if v})
    hidden = {s.lower() for s in _HIDDEN_SCHEMAS.get(dialect, set())}
    try:
        all_schemas = inspector.get_schema_names()
    except NotImplementedError:
        return [None]
    kept = [s for s in all_schemas if s and s.lower() not in hidden]
    return kept or [None]


def _format_type(t: Any) -> str | None:
    """Best-effort stringification of a SQLAlchemy column type — ``VARCHAR(120)``,
    ``NUMERIC(10, 2)``, ``INTEGER``. ``None`` when we can't render one cleanly."""
    if t is None:
        return None
    try:
        s = str(t)
    except Exception:
        return None
    return s.strip() or None


def _walk_sync(sync_conn, *, dialect: str, pool_schemas: dict[str, str], max_tables: int) -> tuple[list[TableInfo], bool]:
    """Synchronous worker — runs inside ``connection.run_sync()`` so we can use the regular
    (sync) ``Inspector`` API. SQLAlchemy doesn't ship an async Inspector — this is the
    documented pattern."""
    inspector = inspect(sync_conn)
    schemas = _scoped_schemas(inspector, dialect, pool_schemas)
    out: list[TableInfo] = []
    truncated = False
    for sch in schemas:
        try:
            t_names = inspector.get_table_names(schema=sch)
            v_names = inspector.get_view_names(schema=sch)
        except NotImplementedError:
            continue
        for kind, names in (("table", t_names), ("view", v_names)):
            for name in sorted(names):
                if len(out) >= max_tables:
                    truncated = True
                    return out, truncated
                try:
                    cols = inspector.get_columns(name, schema=sch)
                except NotImplementedError:
                    cols = []
                table = TableInfo(
                    name=name,
                    # Only emit `schema` when there's more than one — keeps the wire payload
                    # terse for the common single-schema case (most pools).
                    schema=sch if (sch and len(schemas) > 1) else None,
                    kind=kind,
                    columns=[
                        ColumnInfo(
                            name=str(c.get("name") or ""),
                            type=_format_type(c.get("type")),
                            nullable=bool(c.get("nullable", True)),
                        )
                        for c in cols
                    ],
                )
                out.append(table)
    return out, truncated


async def introspect_pool(
    pools: PoolRegistry, pool_name: str, *, max_tables: int = DEFAULT_MAX_TABLES,
) -> dict[str, Any]:
    """Return ``{pool, dialect, tables: [...], truncated: bool}`` for *pool_name*.

    Opens a connection from the pool's engine (creating it if needed), runs the inspector
    in a thread under ``connection.run_sync()`` to dodge the missing async-inspector API,
    and returns plain dicts ready for ``JSONResponse``. Raises :class:`UnknownPoolError` /
    :class:`SQLAlchemyError` like any other pool operation; the route translates those.
    """
    dialect = pools.dialect(pool_name)
    pool_schemas = pools.schemas(pool_name)
    engine: AsyncEngine = pools.engine(pool_name)
    async with engine.connect() as conn:
        tables, truncated = await conn.run_sync(
            _walk_sync, dialect=dialect, pool_schemas=pool_schemas, max_tables=max_tables,
        )
    return {
        "pool": pool_name,
        "dialect": dialect,
        "tables": [t.to_dict() for t in tables],
        "truncated": truncated,
    }


__all__ = ["ColumnInfo", "TableInfo", "introspect_pool", "DEFAULT_MAX_TABLES"]


# `asyncio` is imported so the public docstring's ``await introspect_pool(...)`` example reads
# naturally for callers from a sync context; not used here directly. Avoid the unused-import warning:
_ = asyncio
