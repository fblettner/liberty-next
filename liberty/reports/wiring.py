"""Lifecycle wiring for the report registry — called from the FastAPI lifespan
and from ``POST /admin/reload``.

The registry is stateless w.r.t. the framework's other registries (connectors,
screens, jobs, …) — it just holds :class:`ReportDef` instances. So rebuilding
it is cheap and atomic: drop the old, swap in the new on ``app.state.reports``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from liberty.licensing import LicenseResult
from liberty.reports.registry import ReportRegistry, discover_reports

if TYPE_CHECKING:
    from fastapi import FastAPI

    from liberty.config import Settings

_log = logging.getLogger(__name__)


def build_reports(
    settings: "Settings",
    license: LicenseResult | None,
) -> ReportRegistry:
    """Build a fresh :class:`ReportRegistry`. Called once from the FastAPI
    lifespan after the license + connectors are ready.

    *settings* is accepted (and currently unused) so the signature matches
    the other ``build_*`` helpers and stays forward-compatible: future
    options (custom ``reports_dir`` override etc.) will land on
    ``settings.reports``.
    """
    del settings  # reserved for future settings.reports.* knobs
    registry = discover_reports(license=license)
    _log.info(
        "reports: registry ready — %d report(s) across %d scope(s)",
        len(registry.names()),
        len({d.scope for d in registry.all()}),
    )
    return registry


def refresh_reports(
    app: "FastAPI",
    settings: "Settings",
    license: LicenseResult | None,
) -> ReportRegistry:
    """Rebuild the registry and atomically swap it onto ``app.state.reports``.
    Called by ``POST /admin/reload`` after the license / connectors swap so a
    license rotation that newly covers ``nomasx1`` immediately unlocks its
    reports (and vice versa).

    Returns the new registry so the reload endpoint can surface its size in
    the response payload."""
    new = build_reports(settings, license)
    app.state.reports = new
    return new
