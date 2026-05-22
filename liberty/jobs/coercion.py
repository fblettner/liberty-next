"""JDE-coercion rules for ``sql_copy`` — see ``docs/PHASE13.md`` §5.1.

Pure functions on purpose: the type-mapping logic and the per-row coercion are
the highest-stakes part of the JDE sync (a wrong mapping silently corrupts the
nomajde target tables), so they live apart from the executor and get
exhaustive table-driven tests in ``test_jobs_coercion.py``.

The two callers:

* :func:`derive_target_pg_type` — at copy start, drives the ``CREATE TABLE``
  for the target ``__new`` work table.
* :func:`coerce_row` — at copy time, applied to every batch before INSERT.

Both take ``type_coercion`` (``"jde"`` or ``"none"`` today) and a
:class:`~liberty.jobs.schema.DecimalMode`; ``"none"`` is a passthrough that
preserves source column names + types, ``"jde"`` applies the v1 ``db_copy.py``
rules (Decimal(p,0)→int/bigint, Decimal(p,s>0)→bigint *or* numeric(p,s)
depending on decimal_mode, strings trimmed + null-stripped, column names
lowercased).

The "why" for the Decimal default is in PHASE13 §5.1 + the project memory at
``project_jde_decimal_coercion.md`` — TL;DR JDE never stores decimals, the
truncating cast is the workaround for Spark→Postgres mapping NUMBER → float.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy.types import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    TypeEngine,
)

from liberty.jobs.schema import DecimalMode

JDE_COERCION = "jde"
NO_COERCION = "none"


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """A copy target's schema needs to know each source column's name + type
    (nullability is preserved on a best-effort basis; v1 didn't enforce it
    either). The :class:`~sqlalchemy.types.TypeEngine` here is whatever
    :meth:`Inspector.get_columns` returned — driver-specific subclasses are
    fine, the JDE rules check via :func:`isinstance` against the public
    SQLAlchemy types in this module."""

    name: str
    type: TypeEngine
    nullable: bool = True


# --------------------------------------------------------------------------- #
# column name normalisation
# --------------------------------------------------------------------------- #


def normalize_column_name(name: str, *, type_coercion: str) -> str:
    """JDE convention: target column names are lowercase. Passthrough otherwise.

    The lowercasing is what v1's ``db_copy.lowercase_columns`` did; it survives
    here so the existing nomajde Postgres tables don't see their columns
    renamed across the cutover."""
    if type_coercion == JDE_COERCION:
        return name.lower()
    return name


# --------------------------------------------------------------------------- #
# type mapping (source SQLAlchemy type → target Postgres DDL string)
# --------------------------------------------------------------------------- #


def derive_target_pg_type(
    source_type: TypeEngine,
    *,
    type_coercion: str,
    decimal_mode: DecimalMode = DecimalMode.TRUNCATE,
) -> str:
    """Map a SQLAlchemy column type to the target Postgres DDL type string.

    ``type_coercion = "none"`` → render the source type as-is (SQLAlchemy
    compiles it for the generic SQL dialect; the target driver re-interprets).

    ``type_coercion = "jde"`` → apply the rules from the §5.1 table:

    +------------------------------+--------------------------------+
    | Source                       | Target                         |
    +==============================+================================+
    | ``Decimal(p, 0)`` (any p)    | ``bigint`` — every integer-    |
    |                              | valued JDE column; never       |
    |                              | ``integer`` (int32 overflows)  |
    +------------------------------+--------------------------------+
    | ``Decimal(p, s)``, s > 0     | ``bigint`` (decimal_mode=      |
    |                              | TRUNCATE — default) or         |
    |                              | ``numeric(p, s)`` (PRESERVE)   |
    +------------------------------+--------------------------------+
    | ``Integer`` / ``SmallInteger`` | ``bigint`` — Oracle reflects  |
    | / ``BigInteger``             | NUMBER(p,0) as a generic       |
    |                              | Integer that can still hold a  |
    |                              | 10+-digit value                |
    +------------------------------+--------------------------------+
    | ``String(n)`` / ``VARCHAR``  | ``varchar(n)`` (``text`` if    |
    |                              | length is unset)               |
    +------------------------------+--------------------------------+
    | ``Text``                     | ``text``                       |
    +------------------------------+--------------------------------+
    | ``Date``                     | ``date``                       |
    +------------------------------+--------------------------------+
    | ``DateTime(tz=True)``        | ``timestamptz``                |
    +------------------------------+--------------------------------+
    | ``DateTime``                 | ``timestamp``                  |
    +------------------------------+--------------------------------+
    | ``Time``                     | ``time``                       |
    +------------------------------+--------------------------------+
    | ``Boolean``                  | ``boolean``                    |
    +------------------------------+--------------------------------+
    | ``LargeBinary``              | ``bytea``                      |
    +------------------------------+--------------------------------+
    | anything else                | ``text`` (a safe coercion that |
    |                              | accepts any value)             |
    +------------------------------+--------------------------------+
    """
    if type_coercion != JDE_COERCION:
        return _passthrough_type(source_type)

    # Decimal/Numeric — the JDE-specific rules. NB: SQLAlchemy's Oracle dialect
    # returns NUMBER as ``Numeric``, which is the right hook for this rule.
    if isinstance(source_type, Numeric) and not isinstance(source_type, (Integer, BigInteger, SmallInteger)):
        scale = source_type.scale or 0
        if scale == 0:
            # Integer-valued → bigint, unconditionally. The earlier rule mapped
            # precision ≤ 9 to a 32-bit `integer`, but JDE columns routinely
            # hold values past int32's 2.1-billion ceiling (ids, timestamps),
            # and Oracle's reflected precision doesn't reliably bound them — an
            # asyncpg "value out of int32 range" DataError is the result.
            # bigint (int64, ~9.2e18) can't overflow on any realistic JDE
            # value; the 4-byte-vs-8-byte saving isn't worth a failed sync.
            return "bigint"
        precision = source_type.precision or 38  # NUMBER without precision → max
        if decimal_mode is DecimalMode.PRESERVE:
            return f"numeric({precision}, {scale})"
        # TRUNCATE — the v1 LongType-cast equivalent; intentional, see module docstring
        return "bigint"

    # Any integer-typed source → bigint too. Oracle reflects NUMBER(p,0) as a
    # generic Integer; that "integer" can still carry a 10+-digit value, so a
    # 32-bit Postgres `integer` would overflow. Same reasoning as above.
    if isinstance(source_type, (Integer, BigInteger, SmallInteger)):
        return "bigint"
    if isinstance(source_type, String):
        # NB: Text is a subclass of String in SQLAlchemy; check it first.
        if isinstance(source_type, Text):
            return "text"
        length = getattr(source_type, "length", None)
        return f"varchar({length})" if length else "text"
    if isinstance(source_type, Text):  # pragma: no cover — caught above, defence in depth
        return "text"
    if isinstance(source_type, DateTime):
        return "timestamptz" if source_type.timezone else "timestamp"
    if isinstance(source_type, Date):
        return "date"
    if isinstance(source_type, Time):
        return "time"
    if isinstance(source_type, Boolean):
        return "boolean"
    if isinstance(source_type, LargeBinary):
        return "bytea"
    # Anything we don't recognise — fall back to text. The alternative is to
    # raise, but v1 silently passed unknown types through; mirroring that for
    # cutover parity, with the safer choice of text (accepts any string repr).
    return "text"


def _passthrough_type(source_type: TypeEngine) -> str:
    """Render the source type's generic SQL representation (used when
    ``type_coercion = "none"`` so non-JDE copies preserve types literally)."""
    # ``compile()`` on a TypeEngine returns its SQL type string. We force the
    # generic dialect because the target may be a different driver than the
    # source and we don't want Oracle-isms leaking into a Postgres DDL.
    return str(source_type.compile())


# --------------------------------------------------------------------------- #
# per-row coercion
# --------------------------------------------------------------------------- #


def coerce_row(
    row: dict[str, Any],
    columns: Sequence[ColumnInfo],
    *,
    type_coercion: str,
    decimal_mode: DecimalMode = DecimalMode.TRUNCATE,
    trim_strings: bool = False,
    coalesce_nulls: bool = False,
) -> dict[str, Any]:
    """Transform one source-row mapping into a target-row mapping for a copy.

    Three independent layers, applied in order:

    1. **Source read** — when *trim_strings* (the **source** pool's
       ``trim_strings`` setting), trailing whitespace is stripped from every
       string cell. This is the Oracle CHAR/NCHAR space-padding cleanup: a
       JDE source pads ``CHAR(10)`` to width, the target (Postgres ``varchar``)
       shouldn't carry the padding.
    2. **JDE coercion** — when ``type_coercion == "jde"``: column names →
       lowercase; ``\\x00`` stripped from strings (JDE data hygiene; Postgres
       text can't hold a null byte anyway); ``Decimal(p, s>0)`` truncated to
       ``int`` under ``decimal_mode = TRUNCATE`` (the v1 LongType cast),
       left intact under ``PRESERVE``.
    3. **Target write** — when *coalesce_nulls* (the **target** pool's
       ``coalesce_nulls`` setting), an empty value is replaced with a
       type-appropriate default: ``" "`` for a string column (``None`` *and*
       ``""`` — Oracle treats ``''`` as NULL, a NOT-NULL CHAR needs a space),
       ``0`` for a numeric column. This is the Postgres→JDE direction —
       padding + null management for an Oracle target.

    Keys are looked up by **source** column name (case-insensitively — drivers
    vary) and written under the **target** name (lowercased for JDE, the source
    name otherwise).
    """
    jde = type_coercion == JDE_COERCION
    out: dict[str, Any] = {}
    for col in columns:
        # Source drivers may return the column with any casing — try the
        # column-defined name, then upper, then lower; the first hit wins.
        if col.name in row:
            val = row[col.name]
        elif col.name.upper() in row:
            val = row[col.name.upper()]
        elif col.name.lower() in row:
            val = row[col.name.lower()]
        else:
            val = None
        target_key = col.name.lower() if jde else col.name

        # 1. source read — trim Oracle CHAR/NCHAR padding
        if trim_strings and isinstance(val, str):
            val = val.rstrip()

        # 2. JDE coercion
        if jde and val is not None:
            if isinstance(val, str):
                val = val.replace("\x00", "")
            elif isinstance(col.type, Numeric) and isinstance(val, (int, float, Decimal)):
                scale = col.type.scale or 0
                if scale > 0 and decimal_mode is DecimalMode.TRUNCATE:
                    # int(123.45) → 123, matching the Spark LongType cast (toward
                    # zero). Drivers vary on the Python type for NUMERIC(p, s>0)
                    # — Oracle gives Decimal, SQLite float; either truncates here.
                    val = int(val)

        # 3. target write — coalesce empties for an Oracle-style target
        if coalesce_nulls:
            if isinstance(col.type, String):
                if val is None or val == "":
                    val = " "
            elif isinstance(col.type, (Numeric, Integer, BigInteger, SmallInteger)):
                if val is None:
                    val = 0

        out[target_key] = val
    return out


# --------------------------------------------------------------------------- #
# DDL composition
# --------------------------------------------------------------------------- #


def build_target_ddl(
    schema: str | None,
    table: str,
    columns: Sequence[ColumnInfo],
    *,
    type_coercion: str,
    decimal_mode: DecimalMode = DecimalMode.TRUNCATE,
) -> str:
    """Compose a ``CREATE TABLE`` DDL string for the target side of a copy.

    Column names go through :func:`normalize_column_name`; types through
    :func:`derive_target_pg_type`. The returned SQL uses double-quoted
    identifiers (Postgres-style) — works on every dialect we target."""
    if not columns:
        raise ValueError("build_target_ddl: at least one column is required")

    qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
    col_lines: list[str] = []
    for col in columns:
        name = normalize_column_name(col.name, type_coercion=type_coercion)
        pg_type = derive_target_pg_type(
            col.type, type_coercion=type_coercion, decimal_mode=decimal_mode,
        )
        col_lines.append(f'    "{name}" {pg_type}')
    return f"CREATE TABLE {qualified} (\n" + ",\n".join(col_lines) + "\n)"


def target_column_names(
    columns: Sequence[ColumnInfo],
    *,
    type_coercion: str,
) -> list[str]:
    """The normalised column names (in source order) — used to build the INSERT
    column list. Kept here so target-name normalisation has one source of truth."""
    return [normalize_column_name(c.name, type_coercion=type_coercion) for c in columns]
