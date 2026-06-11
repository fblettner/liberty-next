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
import re
from collections.abc import Callable
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


_SCHEMA_TOKEN = re.compile(r"^#SCHEMA(?:\.([A-Za-z0-9_]+))?#$", re.IGNORECASE)


def resolve_schema_token(value: str | None, pool_schemas: dict[str, str]) -> str | None:
    """Resolve a ``#SCHEMA.<KEY>#`` placeholder (bare ``#SCHEMA#`` → the ``""`` key) to the pool's
    real schema name via its ``schemas`` map. The config builder's SQL editor passes the PORTABLE
    token from a query's FROM (e.g. ``#SCHEMA.CTL#.F0004``); the introspection walk needs the actual
    owner. A plain schema name, or a token with no mapping, is returned unchanged."""
    if not value:
        return value
    m = _SCHEMA_TOKEN.match(value.strip())
    if not m or not pool_schemas:
        return value
    key = m.group(1) or ""
    for k, v in pool_schemas.items():
        if k.lower() == key.lower():
            return v
    return value


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


def _name_like_predicate(pattern: str | None) -> Callable[[str], bool] | None:
    """Compile a SQL-LIKE-style pattern (``F009%``) into a case-insensitive predicate.
    Returns ``None`` for an empty / unset pattern — caller treats this as "match all".

    Wildcards:
      * ``%`` matches any number of characters (the SQL convention)
      * ``_`` is treated **literally** here — operators routinely have underscores in v1's
        column / table names (``USR_ID``, ``LY_USERS``), so a literal ``_`` is the safer
        interpretation. If they want the SQL ``_`` (one-char) meaning they can use ``.``
        once we expose a regex mode (out of scope today).

    The match is case-insensitive so an Oracle uppercase schema (``F00921``) is found by a
    lowercase pattern (``f009%``)."""
    import re
    pat = (pattern or "").strip()
    if not pat:
        return None
    # Escape regex metachars first, then re-introduce ``%`` → ``.*``.
    regex = re.escape(pat).replace(r"%", ".*")
    rx = re.compile(f"^{regex}$", re.IGNORECASE)
    return lambda name: rx.match(name) is not None


def _walk_sync(
    sync_conn, *, dialect: str, pool_schemas: dict[str, str], max_tables: int,
    only_schema: str | None = None, name_like: str | None = None,
) -> tuple[list[TableInfo], bool]:
    """Synchronous worker — runs inside ``connection.run_sync()`` so we can use the regular
    (sync) ``Inspector`` API. SQLAlchemy doesn't ship an async Inspector — this is the
    documented pattern.

    *only_schema* (optional) scopes the walk to a single schema. For Oracle, where
    ``ALL_TAB_COLUMNS`` enumerates every accessible owner's tables, fetching all schemas
    up front is slow (10+ seconds with hundreds of tables) — the CRUD wizard wants to fetch
    just *one* schema at a time, after the operator picks it.

    *name_like* (optional) SQL-LIKE-style pattern (``F009%``) — applied to **table / view
    names** before ``get_columns`` is called per name, so the per-table column fetch only
    runs for the names that matched. That's where the time goes on a schema with hundreds
    of tables (SY920 has 2000+), so this is the big speedup knob. Empty / unset matches all.
    """
    inspector = inspect(sync_conn)
    schemas = _scoped_schemas(inspector, dialect, pool_schemas)
    if only_schema is not None:
        # Case-insensitive match — Oracle's get_schema_names returns ``SY920`` but the operator
        # may have typed ``sy920``. PostgreSQL keeps the case the dictionary stores.
        wanted = only_schema.lower()
        schemas = [s for s in schemas if (s or "").lower() == wanted]
        if not schemas:
            return [], False
    name_match = _name_like_predicate(name_like)
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
                # Drop names that don't match the LIKE pattern before paying for get_columns
                # — that's where the wall-clock goes on a big schema.
                if name_match is not None and not name_match(name):
                    continue
                if len(out) >= max_tables:
                    truncated = True
                    return out, truncated
                try:
                    cols = inspector.get_columns(name, schema=sch)
                except NotImplementedError:
                    cols = []
                table = TableInfo(
                    name=name,
                    # Always emit ``schema`` when the operator scoped the walk to one — they
                    # asked for that specific schema, so the wire payload should be explicit.
                    # Otherwise only emit it when the pool spans multiple schemas (the
                    # original single-schema-pool terseness rule still applies).
                    schema=sch if (sch and (only_schema is not None or len(schemas) > 1)) else None,
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


def _list_schemas_sync(sync_conn, *, dialect: str, pool_schemas: dict[str, str]) -> list[str]:
    """List the non-system schema names a pool exposes — no table walk, no column walk.
    Fast even on Oracle (a single ``ALL_USERS`` query, no per-schema fan-out). Used by the
    CRUD wizard to populate its schema picker before pulling tables for the chosen one."""
    inspector = inspect(sync_conn)
    schemas = _scoped_schemas(inspector, dialect, pool_schemas)
    # ``_scoped_schemas`` may return ``[None]`` when the inspector can't enumerate (SQLite).
    # Drop the None — the wire shape is ``list[str]`` and the SQLite case is already handled
    # by the empty-schemas branch on the wizard side (no picker).
    return [s for s in schemas if s]


async def introspect_pool(
    pools: PoolRegistry, pool_name: str, *,
    max_tables: int = DEFAULT_MAX_TABLES,
    only_schema: str | None = None, name_like: str | None = None,
) -> dict[str, Any]:
    """Return ``{pool, dialect, tables: [...], truncated: bool}`` for *pool_name*.

    Opens a connection from the pool's engine (creating it if needed), runs the inspector
    in a thread under ``connection.run_sync()`` to dodge the missing async-inspector API,
    and returns plain dicts ready for ``JSONResponse``. Raises :class:`UnknownPoolError` /
    :class:`SQLAlchemyError` like any other pool operation; the route translates those.

    *only_schema* scopes the walk to a single schema — Oracle's ``ALL_TAB_COLUMNS``
    enumerates every accessible owner, so the unscoped call is slow when many schemas exist
    (e.g. JDE's PS920 / SY920 / DTA920 / CTL920 / CRP920 / PRD920 each with hundreds of
    tables). The CRUD wizard uses this to fetch tables one schema at a time.

    *name_like* is a SQL-LIKE-style pattern (``F009%``) applied to table / view names —
    the wizard sets it from a free-text input so an operator on a 2000-table SY920 doesn't
    have to wait for every column on every table. Empty matches all (the default).
    """
    dialect = pools.dialect(pool_name)
    pool_schemas = pools.schemas(pool_name)
    engine: AsyncEngine = pools.engine(pool_name)
    async with engine.connect() as conn:
        tables, truncated = await conn.run_sync(
            _walk_sync, dialect=dialect, pool_schemas=pool_schemas, max_tables=max_tables,
            only_schema=only_schema, name_like=name_like,
        )
    return {
        "pool": pool_name,
        "dialect": dialect,
        "tables": [t.to_dict() for t in tables],
        "truncated": truncated,
    }


async def list_pool_schemas(pools: PoolRegistry, pool_name: str) -> dict[str, Any]:
    """Return ``{pool, dialect, schemas: [...]}`` for *pool_name* — just the schema names,
    no tables / columns. Fast lookup, even on Oracle (a single ``ALL_USERS`` query under the
    hood, no per-schema fan-out). Driven by the CRUD wizard's schema picker so the operator
    can pick a target schema *before* paying for the per-schema table walk.

    For pools with a ``[pools.X.schemas]`` map, the listed schemas are the mapped *targets*
    (not the placeholder keys) so what the wizard shows lines up with what the SQL connector
    actually uses when expanding ``#SCHEMA.<NAME>#`` placeholders.
    """
    dialect = pools.dialect(pool_name)
    pool_schemas = pools.schemas(pool_name)
    engine: AsyncEngine = pools.engine(pool_name)
    async with engine.connect() as conn:
        names = await conn.run_sync(_list_schemas_sync, dialect=dialect, pool_schemas=pool_schemas)
    # ``schema_map`` — the ``#SCHEMA.<KEY># → real schema`` mapping so the builder's wizard can offer
    # the PORTABLE token in its schema picker (and keep it in generated SQL) instead of the raw owner.
    return {"pool": pool_name, "dialect": dialect, "schemas": names, "schema_map": dict(pool_schemas)}


__all__ = ["ColumnInfo", "TableInfo", "introspect_pool", "list_pool_schemas", "DEFAULT_MAX_TABLES"]


# `asyncio` is imported so the public docstring's ``await introspect_pool(...)`` example reads
# naturally for callers from a sync context; not used here directly. Avoid the unused-import warning:
_ = asyncio
