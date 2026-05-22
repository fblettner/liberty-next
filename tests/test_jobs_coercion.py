"""Pure-function tests for :mod:`liberty.jobs.coercion` — the JDE rules.

These are the highest-stakes part of nomaflow (a wrong mapping silently
corrupts the nomajde target tables across a daily sync), so the rules get a
table-driven test per outcome. No DB, no executor — just the functions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
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
    VARCHAR,
)

from liberty.jobs import DecimalMode
from liberty.jobs.coercion import (
    JDE_COERCION,
    NO_COERCION,
    ColumnInfo,
    build_target_ddl,
    coerce_row,
    derive_target_pg_type,
    normalize_column_name,
    target_column_names,
)


# --------------------------------------------------------------------------- #
# normalize_column_name
# --------------------------------------------------------------------------- #


def test_normalize_lowercases_for_jde() -> None:
    assert normalize_column_name("UPMJ", type_coercion=JDE_COERCION) == "upmj"
    assert normalize_column_name("MixedCase", type_coercion=JDE_COERCION) == "mixedcase"


def test_normalize_passthrough_for_none() -> None:
    assert normalize_column_name("UPMJ", type_coercion=NO_COERCION) == "UPMJ"
    assert normalize_column_name("MixedCase", type_coercion=NO_COERCION) == "MixedCase"


# --------------------------------------------------------------------------- #
# derive_target_pg_type — JDE rules table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source_type, expected_pg", [
    # Decimal(p, 0): p ≤ 9 → integer
    (Numeric(precision=5, scale=0), "integer"),
    (Numeric(precision=9, scale=0), "integer"),
    # Decimal(p, 0): p > 9 → bigint
    (Numeric(precision=10, scale=0), "bigint"),
    (Numeric(precision=18, scale=0), "bigint"),
    # Decimal(p, s>0) → bigint (default TRUNCATE mode)
    (Numeric(precision=10, scale=2), "bigint"),
    # Other integer types
    (Integer(), "integer"),
    (SmallInteger(), "integer"),
    (BigInteger(), "bigint"),
    # Strings
    (String(50), "varchar(50)"),
    (String(), "text"),
    (VARCHAR(100), "varchar(100)"),
    (Text(), "text"),
    # Dates / times
    (Date(), "date"),
    (DateTime(), "timestamp"),
    (DateTime(timezone=True), "timestamptz"),
    (Time(), "time"),
    # Other primitives
    (Boolean(), "boolean"),
    (LargeBinary(), "bytea"),
])
def test_derive_target_pg_type_jde(source_type, expected_pg) -> None:
    assert derive_target_pg_type(source_type, type_coercion=JDE_COERCION) == expected_pg


def test_decimal_preserve_mode_keeps_scale() -> None:
    """The §5.1 decision — `decimal_mode = "preserve"` maps Decimal(p, s>0) to
    numeric(p, s) instead of truncating to bigint. Only kicks in on JDE rules."""
    assert derive_target_pg_type(
        Numeric(precision=10, scale=2),
        type_coercion=JDE_COERCION,
        decimal_mode=DecimalMode.PRESERVE,
    ) == "numeric(10, 2)"
    # s=0 is unaffected by decimal_mode (no decimal places to preserve)
    assert derive_target_pg_type(
        Numeric(precision=8, scale=0),
        type_coercion=JDE_COERCION,
        decimal_mode=DecimalMode.PRESERVE,
    ) == "integer"


def test_decimal_with_no_precision_treated_as_max() -> None:
    """Oracle NUMBER without a precision arrives as Numeric(None, None);
    treat it as the largest reasonable bigint slot rather than crashing."""
    # scale=None → 0; precision=None → 38 → > 9 → bigint
    assert derive_target_pg_type(
        Numeric(precision=None, scale=None),
        type_coercion=JDE_COERCION,
    ) == "bigint"


def test_unknown_type_falls_back_to_text() -> None:
    """Mirror v1's "pass unknown types through" behavior with the safest
    choice (text accepts anything stringifiable)."""

    class WeirdType:
        # Not a TypeEngine subclass; the isinstance checks all miss.
        pass

    assert derive_target_pg_type(WeirdType(), type_coercion=JDE_COERCION) == "text"  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# coerce_row — per-row value coercion
# --------------------------------------------------------------------------- #


def _cols(*pairs: tuple[str, object]) -> list[ColumnInfo]:
    return [ColumnInfo(name=n, type=t) for n, t in pairs]  # type: ignore[arg-type]


def test_coerce_row_lowercases_keys_for_jde() -> None:
    cols = _cols(("UPMJ", Integer()), ("MIXEDCASE", String(10)))
    row = {"UPMJ": 12345, "MIXEDCASE": "hello"}
    assert coerce_row(row, cols, type_coercion=JDE_COERCION) == {
        "upmj": 12345, "mixedcase": "hello",
    }


def test_coerce_row_preserves_keys_for_none() -> None:
    cols = _cols(("UPMJ", Integer()))
    row = {"UPMJ": 12345}
    assert coerce_row(row, cols, type_coercion=NO_COERCION) == {"UPMJ": 12345}


def test_coerce_row_trims_and_strips_null_bytes() -> None:
    """v1 db_copy: trim + regexp_replace('\\x00', ''). Match per-value."""
    cols = _cols(("NAME", String(50)))
    row = {"NAME": "  hello \x00world   \x00\x00"}
    # Note: rstrip() only — leading whitespace is NOT stripped (matches v1 trim()
    # semantics: most JDE string values are left-aligned + space-padded right).
    assert coerce_row(row, cols, type_coercion=JDE_COERCION) == {
        "name": "  hello world",
    }


def test_coerce_row_decimal_truncate_default() -> None:
    cols = _cols(("AMOUNT", Numeric(precision=10, scale=2)))
    row = {"AMOUNT": Decimal("123.45")}
    assert coerce_row(row, cols, type_coercion=JDE_COERCION) == {"amount": 123}
    # Negative truncates toward zero (matches int() semantics + Spark LongType)
    row = {"AMOUNT": Decimal("-123.99")}
    assert coerce_row(row, cols, type_coercion=JDE_COERCION) == {"amount": -123}


def test_coerce_row_decimal_preserve_keeps_value() -> None:
    cols = _cols(("AMOUNT", Numeric(precision=10, scale=2)))
    row = {"AMOUNT": Decimal("123.45")}
    assert coerce_row(
        row, cols, type_coercion=JDE_COERCION, decimal_mode=DecimalMode.PRESERVE,
    ) == {"amount": Decimal("123.45")}


def test_coerce_row_decimal_with_zero_scale_passes_through_as_decimal() -> None:
    """Decimal(p, 0) values stay as Decimal — the target column type does the
    integer storage. The truncation only applies when scale > 0."""
    cols = _cols(("COUNT", Numeric(precision=5, scale=0)))
    row = {"COUNT": Decimal("42")}
    out = coerce_row(row, cols, type_coercion=JDE_COERCION)
    # int(Decimal('42')) and Decimal('42') are equal under == but typed differently;
    # the coercer leaves scale=0 Decimals alone, asyncpg/aiosqlite handle the cast.
    assert out["count"] == 42


def test_coerce_row_handles_none_values() -> None:
    cols = _cols(("OPT", String(10)), ("NUM", Numeric(precision=8, scale=2)))
    row = {"OPT": None, "NUM": None}
    assert coerce_row(row, cols, type_coercion=JDE_COERCION) == {"opt": None, "num": None}


def test_coerce_row_passthrough_does_not_mutate_caller_dict() -> None:
    cols = _cols(("X", String(10)))
    original = {"X": "value"}
    out = coerce_row(original, cols, type_coercion=NO_COERCION)
    out["X"] = "modified"
    assert original["X"] == "value"


def test_coerce_row_looks_up_source_key_case_insensitively() -> None:
    """A column declared as ``UPMJ`` should match a row delivered with key
    ``upmj`` (some drivers return lowercase) — the executor shouldn't have to
    know which one it'll get."""
    cols = _cols(("UPMJ", Integer()))
    assert coerce_row({"upmj": 1}, cols, type_coercion=JDE_COERCION) == {"upmj": 1}
    assert coerce_row({"UPMJ": 2}, cols, type_coercion=JDE_COERCION) == {"upmj": 2}


# --------------------------------------------------------------------------- #
# build_target_ddl
# --------------------------------------------------------------------------- #


def test_build_ddl_jde_with_schema() -> None:
    cols = _cols(
        ("UPMJ", Integer()),
        ("AMOUNT", Numeric(precision=10, scale=2)),
        ("NAME", String(50)),
    )
    ddl = build_target_ddl("nomajde", "f0901", cols, type_coercion=JDE_COERCION)
    assert ddl == (
        'CREATE TABLE "nomajde"."f0901" (\n'
        '    "upmj" integer,\n'
        '    "amount" bigint,\n'
        '    "name" varchar(50)\n'
        ')'
    )


def test_build_ddl_jde_preserve_decimal_mode() -> None:
    cols = _cols(("AMT", Numeric(precision=12, scale=4)))
    ddl = build_target_ddl(
        None, "t", cols,
        type_coercion=JDE_COERCION, decimal_mode=DecimalMode.PRESERVE,
    )
    assert '"amt" numeric(12, 4)' in ddl


def test_build_ddl_no_columns_raises() -> None:
    with pytest.raises(ValueError):
        build_target_ddl(None, "t", [], type_coercion=JDE_COERCION)


def test_build_ddl_no_schema() -> None:
    cols = _cols(("X", Integer()))
    ddl = build_target_ddl(None, "t", cols, type_coercion=JDE_COERCION)
    assert ddl.startswith('CREATE TABLE "t" (')


# --------------------------------------------------------------------------- #
# target_column_names
# --------------------------------------------------------------------------- #


def test_target_column_names_lowercases_for_jde() -> None:
    cols = _cols(("UPMJ", Integer()), ("MixedCase", String(10)))
    assert target_column_names(cols, type_coercion=JDE_COERCION) == ["upmj", "mixedcase"]


def test_target_column_names_passthrough_for_none() -> None:
    cols = _cols(("UPMJ", Integer()), ("MixedCase", String(10)))
    assert target_column_names(cols, type_coercion=NO_COERCION) == ["UPMJ", "MixedCase"]
