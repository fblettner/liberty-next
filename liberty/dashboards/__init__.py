"""Dashboards — Phase 8 slice 3. A *dashboard* is a layout of widgets on a 12-column grid,
referenced by id from a menu like any other screen. Two widget kinds in slice 3a (this
module): **chart** (a saved chart by id, or an inline spec) and **kpi** (a single
aggregated number). See :mod:`liberty.dashboards.config` for the file shape.
"""

from __future__ import annotations

from liberty.dashboards.config import (
    ChartWidget,
    Dashboard,
    DashboardFilter,
    DashboardFilterOptions,
    DashboardsFile,
    KpiWidget,
    TableWidget,
    Widget,
    WidgetBase,
    load_dashboards,
    parse_dashboards,
)

__all__ = [
    "ChartWidget",
    "Dashboard",
    "DashboardFilter",
    "DashboardFilterOptions",
    "DashboardsFile",
    "KpiWidget",
    "TableWidget",
    "Widget",
    "WidgetBase",
    "load_dashboards",
    "parse_dashboards",
]
