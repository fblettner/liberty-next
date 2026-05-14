"""Unit tests for ``liberty.dashboards.config`` — the ``DashboardsFile`` parser + validator.

The runtime side (``GET /api/dashboards`` permission filtering, chart reference resolution,
admin/parsed round-trip) lives in ``test_web_dashboards.py`` and ``test_web_admin.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from liberty.dashboards import (
    ChartWidget,
    DashboardsFile,
    KpiWidget,
    load_dashboards,
    parse_dashboards,
)


def test_parse_minimal_dashboard() -> None:
    data = {
        "dashboards": {
            "overview": {
                "label": "Overview",
                "widgets": [
                    {"type": "chart", "chart": "users_per_app", "col_span": 6, "row_span": 1},
                    {"type": "kpi", "label": "Active users", "connector": "nomasx1",
                     "query": "security_users_get", "column": "USR_ID", "aggregation": "count"},
                ],
            },
        },
    }
    df = parse_dashboards(data)
    d = df.dashboards["overview"]
    # id is injected from the dict key
    assert d.id == "overview"
    assert len(d.widgets) == 2
    chart_w, kpi_w = d.widgets
    assert isinstance(chart_w, ChartWidget) and chart_w.chart == "users_per_app"
    assert chart_w.col_span == 6 and chart_w.row_span == 1
    assert isinstance(kpi_w, KpiWidget) and kpi_w.aggregation == "count" and kpi_w.column == "USR_ID"
    # KPI defaults the grid placement when unspecified
    assert kpi_w.col_span == 6 and kpi_w.row_span == 1


def test_chart_widget_inline_or_reference_not_both() -> None:
    """A chart widget must pick exactly one mode — either `chart = "<id>"` or an inline triple
    (connector + query + spec). Both or neither → reject (a typo otherwise silently disappears)."""
    both = {
        "dashboards": {
            "x": {
                "label": "X",
                "widgets": [{
                    "type": "chart", "chart": "saved",
                    "connector": "c", "query": "q",
                    "spec": {"type": "bar", "x": "X", "y": ["Y"], "aggregation": "count"},
                }],
            },
        },
    }
    with pytest.raises(Exception, match=r"either"):
        parse_dashboards(both)
    neither = {"dashboards": {"x": {"label": "X", "widgets": [{"type": "chart"}]}}}
    with pytest.raises(Exception, match=r"missing"):
        parse_dashboards(neither)


def test_chart_widget_inline_requires_full_triple() -> None:
    """A partial inline (`query` without `spec`) makes the runtime unable to render — rejected."""
    data = {
        "dashboards": {
            "x": {
                "label": "X",
                "widgets": [{
                    "type": "chart", "connector": "c", "query": "q",
                    # missing `spec`
                }],
            },
        },
    }
    with pytest.raises(Exception, match=r"connector.*query.*spec"):
        parse_dashboards(data)


def test_col_span_bounds() -> None:
    """col_span out of [1, 12] → 422; same for row_span out of [1, 8]. Catches a typo before
    the dashboard renders into a broken layout."""
    bad = {
        "dashboards": {
            "x": {
                "label": "X",
                "widgets": [{
                    "type": "chart", "chart": "c", "col_span": 0,
                }],
            },
        },
    }
    with pytest.raises(Exception):
        parse_dashboards(bad)
    too_wide = {
        "dashboards": {
            "x": {
                "label": "X",
                "widgets": [{
                    "type": "chart", "chart": "c", "col_span": 13,
                }],
            },
        },
    }
    with pytest.raises(Exception):
        parse_dashboards(too_wide)


def test_id_mismatch_with_key_rejected() -> None:
    data = {
        "dashboards": {
            "overview": {"id": "different", "label": "X", "widgets": []},
        },
    }
    with pytest.raises(Exception, match="must match its key"):
        parse_dashboards(data)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    df = load_dashboards(tmp_path / "nope.toml")
    assert isinstance(df, DashboardsFile)
    assert df.dashboards == {}


def test_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "dashboards.toml"
    p.write_text("""
[dashboards.security_overview]
label = "Security Overview"
description = "User counts + assignments at a glance"

  [[dashboards.security_overview.widgets]]
  type = "chart"
  chart = "users_per_app"
  col_span = 6
  row_span = 1

  [[dashboards.security_overview.widgets]]
  type = "kpi"
  label = "Active users"
  connector = "nomasx1"
  query = "security_users_get"
  column = "USR_ID"
  aggregation = "count"
  col_span = 3
""")
    df = load_dashboards(p)
    d = df.dashboards["security_overview"]
    assert d.description == "User counts + assignments at a glance"
    assert len(d.widgets) == 2
    assert isinstance(d.widgets[1], KpiWidget) and d.widgets[1].label == "Active users"
