"""Unit tests for ``liberty.charts.config`` — the ``ChartsFile`` parser + validator.

The runtime side (``GET /api/charts`` permission filtering, the ``/admin/config/charts/parsed``
PUT round-trip) is covered in ``test_web_charts.py`` and ``test_web_admin.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from liberty.charts import ChartsFile, load_charts, parse_charts


def test_parse_minimal_chart() -> None:
    """One [charts.<id>] with a spec is enough — every optional field is left default."""
    data = {
        "charts": {
            "users_per_app": {
                "label": "Users per application",
                "connector": "nomasx1",
                "query": "security_users_get",
                "spec": {"type": "bar", "x": "APPS_ID", "y": ["USR_ID"], "aggregation": "count"},
            },
        },
    }
    cf = parse_charts(data)
    assert set(cf.charts) == {"users_per_app"}
    c = cf.charts["users_per_app"]
    # `id` is injected from the dict key when omitted — same convention as screens/menus.
    assert c.id == "users_per_app"
    assert c.label == "Users per application"
    assert c.description is None
    assert c.connector == "nomasx1" and c.query == "security_users_get"
    assert c.spec.type == "bar" and c.spec.x == "APPS_ID" and c.spec.y == ["USR_ID"]
    assert c.spec.aggregation == "count"
    # the optional flags default to None (so model_dump(exclude_none=True) keeps the file terse)
    assert c.spec.stacked is None and c.spec.show_legend is None and c.spec.show_grid is None


def test_id_mismatch_with_key_rejected() -> None:
    """An explicit ``id`` field must match its dict key — a mismatch is rejected (the convention
    other config sections follow). Matches keep the file diff-friendly."""
    data = {
        "charts": {
            "users": {
                "id": "different",
                "label": "Users", "connector": "c", "query": "q",
                "spec": {"type": "bar", "x": "X", "y": ["Y"]},
            },
        },
    }
    with pytest.raises(Exception, match="must match its key"):
        parse_charts(data)


def test_missing_y_rejected() -> None:
    """A chart with no Y column wouldn't render anything — fail loudly at parse time so the
    operator fixes it before saving (the frontend can keep a half-built spec while editing —
    that's a UI state concern, not a config-file concern)."""
    data = {
        "charts": {
            "broken": {
                "label": "Broken", "connector": "c", "query": "q",
                "spec": {"type": "bar", "x": "X", "y": []},
            },
        },
    }
    with pytest.raises(Exception, match="spec.y"):
        parse_charts(data)


def test_missing_x_rejected() -> None:
    data = {
        "charts": {
            "broken": {
                "label": "Broken", "connector": "c", "query": "q",
                "spec": {"type": "bar", "x": "", "y": ["Y"]},
            },
        },
    }
    with pytest.raises(Exception, match="spec.x"):
        parse_charts(data)


def test_extra_field_rejected() -> None:
    """``extra="forbid"`` — typos at the top of the spec (e.g. `kind` instead of `type`) fail
    loud rather than silently parse to a default. Caught the same way for ChartConfig itself."""
    data = {
        "charts": {
            "c": {
                "label": "C", "connector": "c", "query": "q",
                "spec": {"type": "bar", "x": "X", "y": ["Y"], "kind": "oops"},
            },
        },
    }
    with pytest.raises(Exception, match="kind"):
        parse_charts(data)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    """A non-existent ``charts.toml`` yields an empty :class:`ChartsFile` (same as missing
    menus/screens — the operator just hasn't saved any charts yet)."""
    cf = load_charts(tmp_path / "nope.toml")
    assert isinstance(cf, ChartsFile)
    assert cf.charts == {}


def test_load_roundtrip(tmp_path: Path) -> None:
    """A small ``charts.toml`` written from a Pydantic ChartsFile round-trips through load_charts."""
    p = tmp_path / "charts.toml"
    p.write_text("""
[charts.users_per_app]
label = "Users per app"
description = "Active user count grouped by application"
connector = "nomasx1"
query = "security_users_get"

[charts.users_per_app.spec]
type = "bar"
x = "APPS_ID"
y = ["USR_ID"]
aggregation = "count"
stacked = false
""")
    cf = load_charts(p)
    c = cf.charts["users_per_app"]
    assert c.description == "Active user count grouped by application"
    assert c.spec.stacked is False
