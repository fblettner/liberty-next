"""Charts — Phase 8's saved, operator-curated chart definitions (slice 2).

A chart in v2 is a named, version-controlled view of a connector's query, rendered as a
bar / line / area / pie. The runtime side (the widget itself, the toggle on the TableView)
ships in slice 1 with localStorage persistence; this module loads the *shared* version
from ``config/charts.toml`` so charts can be saved, diffed in git, and referenced from a
menu / dashboard by id.

The TOML shape is in :mod:`liberty.charts.config`. ``GET /api/charts`` (Phase 8 slice 2)
serves the catalog, permission-pruned by each chart's underlying query.
"""

from __future__ import annotations

from liberty.charts.config import (
    Aggregation,
    ChartConfig,
    ChartSpec,
    ChartType,
    ChartsFile,
    load_charts,
    parse_charts,
)

__all__ = [
    "Aggregation",
    "ChartConfig",
    "ChartSpec",
    "ChartType",
    "ChartsFile",
    "load_charts",
    "parse_charts",
]
