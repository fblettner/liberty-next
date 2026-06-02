"""Reports — a first-class concept alongside screens / charts / dashboards / jobs.

Each report is a python callable (declared by a plugin or the customer's own
code) that produces a markdown document plus optional metadata; the framework
renders that to the requested output format (markdown or PDF) and streams it
back to the caller.

Public API:

* :class:`ReportDef` — a declaration (id, scope, title, params, callable,
  output formats).
* :class:`ReportContent` — what every report callable returns: the markdown
  body, optional landscape SVG, title / filename, PDF style overrides.
* :class:`ReportRegistry` — populated at app startup by walking the plugins
  directory; consulted by the web router at request time.
* :func:`render_content` — content → bytes for the requested format. The PDF
  pipeline (WeasyPrint + the in-house CSS) lives in :mod:`liberty.reports.render`.

See :doc:`docs/PHASE-REPORTS.md` (TODO) for the full design.
"""

from liberty.reports.schema import (
    OutputFormat,
    ReportContent,
    ReportDef,
    ReportParam,
    UnknownReportError,
)
from liberty.reports.registry import ReportRegistry, discover_reports
from liberty.reports.render import render_content
from liberty.reports.wiring import build_reports, refresh_reports

__all__ = [
    "OutputFormat",
    "ReportContent",
    "ReportDef",
    "ReportParam",
    "ReportRegistry",
    "UnknownReportError",
    "build_reports",
    "discover_reports",
    "refresh_reports",
    "render_content",
]
