"""Unit tests for ``liberty.charts.config`` — the ``ChartsFile`` parser + validator.

Charts are scoped to a connector — ``[charts.<scope>.<id>]`` — so the top-level dict is two
levels deep: ``charts[scope][id]``. Both ``id`` (inner key) and ``connector`` (scope key) are
injected from the path. The runtime side (``GET /api/charts`` permission filtering, the
``/admin/config/charts/parsed`` PUT round-trip) is covered in ``test_web_charts.py`` and
``test_web_admin.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from liberty.charts import ChartsFile, load_charts, parse_charts


def test_parse_minimal_chart() -> None:
    """One [charts.<scope>.<id>] with a spec is enough — id + connector are injected from the path."""
    data = {
        "charts": {
            "nomasx1": {
                "users_per_app": {
                    "label": "Users per application",
                    "query": "security_users_get",
                    "spec": {"type": "bar", "x": "APPS_ID", "y": ["USR_ID"], "aggregation": "count"},
                },
            },
        },
    }
    cf = parse_charts(data)
    assert set(cf.charts) == {"nomasx1"}
    assert set(cf.charts["nomasx1"]) == {"users_per_app"}
    c = cf.charts["nomasx1"]["users_per_app"]
    # `id` + `connector` are injected from the path keys.
    assert c.id == "users_per_app"
    assert c.connector == "nomasx1"
    assert c.label == "Users per application"
    assert c.description is None
    assert c.query == "security_users_get"
    assert c.spec.type == "bar" and c.spec.x == "APPS_ID" and c.spec.y == ["USR_ID"]
    assert c.spec.aggregation == "count"
    # the optional flags default to None (so model_dump(exclude_none=True) keeps the file terse)
    assert c.spec.stacked is None and c.spec.show_legend is None and c.spec.show_grid is None
    # iter_charts / get_chart helpers
    assert list(cf.iter_charts()) == [("nomasx1", "users_per_app", c)]
    assert cf.get_chart("nomasx1", "users_per_app") is c
    assert cf.get_chart("nomasx1", "nope") is None


def test_id_mismatch_with_key_rejected() -> None:
    """An explicit ``id`` field must match its inner key — a mismatch is rejected."""
    data = {
        "charts": {
            "c": {
                "users": {
                    "id": "different",
                    "label": "Users", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"]},
                },
            },
        },
    }
    with pytest.raises(Exception, match="must match its key"):
        parse_charts(data)


def test_connector_mismatch_with_scope_rejected() -> None:
    """An explicit ``connector`` must match the scope key."""
    data = {
        "charts": {
            "nomasx1": {
                "users": {
                    "connector": "other",
                    "label": "Users", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"]},
                },
            },
        },
    }
    with pytest.raises(Exception, match="must match its scope"):
        parse_charts(data)


def test_missing_y_rejected() -> None:
    """A chart with no Y column wouldn't render anything — fail loudly at parse time."""
    data = {
        "charts": {
            "c": {
                "broken": {
                    "label": "Broken", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": []},
                },
            },
        },
    }
    with pytest.raises(Exception, match="spec.y"):
        parse_charts(data)


def test_missing_x_rejected() -> None:
    data = {
        "charts": {
            "c": {
                "broken": {
                    "label": "Broken", "query": "q",
                    "spec": {"type": "bar", "x": "", "y": ["Y"]},
                },
            },
        },
    }
    with pytest.raises(Exception, match="spec.x"):
        parse_charts(data)


def test_extra_field_rejected() -> None:
    """``extra="forbid"`` — typos at the top of the spec (e.g. `kind` instead of `type`) fail loud."""
    data = {
        "charts": {
            "c": {
                "c": {
                    "label": "C", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"], "kind": "oops"},
                },
            },
        },
    }
    with pytest.raises(Exception, match="kind"):
        parse_charts(data)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    """A non-existent ``charts.toml`` yields an empty :class:`ChartsFile`."""
    cf = load_charts(tmp_path / "nope.toml")
    assert isinstance(cf, ChartsFile)
    assert cf.charts == {}


def test_load_roundtrip(tmp_path: Path) -> None:
    """A small ``charts.toml`` written in the nested shape round-trips through load_charts."""
    p = tmp_path / "charts.toml"
    p.write_text("""
[charts.nomasx1.users_per_app]
label = "Users per app"
description = "Active user count grouped by application"
query = "security_users_get"

[charts.nomasx1.users_per_app.spec]
type = "bar"
x = "APPS_ID"
y = ["USR_ID"]
aggregation = "count"
stacked = false
""")
    cf = load_charts(p)
    c = cf.charts["nomasx1"]["users_per_app"]
    assert c.connector == "nomasx1"
    assert c.description == "Active user count grouped by application"
    assert c.spec.stacked is False
