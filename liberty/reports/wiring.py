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
from liberty.reports.custom import build_custom_report_entries, load_custom_reports
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

    Combines two discovery sources:

    1. Plugin-declared reports — Python ``REPORTS`` lists in
       ``plugins/<scope>/reports/__init__.py``.
    2. Operator-authored custom reports — TOML entries in
       ``settings.reports.templates_config_path`` (Phase 4a). All carry
       ``scope = "custom"`` so a single ``reports:custom:*:run``
       permission grant unlocks every one of them.

    A missing or empty ``reports.toml`` is a no-op — the registry just
    holds the plugin-declared set."""
    registry = discover_reports(license=license)
    try:
        templates = load_custom_reports(settings.reports.templates_config_path)
    except Exception as exc:  # noqa: BLE001 — surface as warning, don't kill startup
        _log.warning(
            "reports: failed to load custom templates from %s — %s",
            settings.reports.templates_config_path, exc,
        )
        templates = []
    if templates:
        try:
            defs, callables = build_custom_report_entries(templates)
            registry.add(defs, callables)
        except ValueError as exc:
            # Duplicate id between a plugin and a custom report. Warn loudly so
            # the operator renames one; the registry is still usable (plugin
            # report wins since it landed first).
            _log.warning(
                "reports: custom templates collide with plugin reports — %s", exc,
            )
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
