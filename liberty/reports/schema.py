"""Report-registry types — Pydantic for the declared shape, dataclass for the
runtime ``ReportContent`` callables return.

A plugin declares its reports via a list of :class:`ReportDef` exposed as
``REPORTS`` from ``plugins/<name>/reports/__init__.py``; the framework reads
them at startup (see :mod:`liberty.reports.registry`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Output formats the framework knows how to render. Adding ``html`` / ``docx``
# later is a framework-layer change in :mod:`liberty.reports.render` — plugins
# don't need to know.
OutputFormat = Literal["markdown", "pdf"]


class UnknownReportError(KeyError):
    """Raised by :class:`ReportRegistry.get` for a missing ``scope:id``. The
    web layer maps this to HTTP 404."""


class ReportParam(BaseModel):
    """One named input the report needs at run time.

    The framework validates the request body against this list before calling
    the report's python callable: required params must be present, types are
    coerced via :mod:`liberty.coercion` (string-from-JSON → declared type).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Parameter key — used in the request body and passed to the callable.")
    label: str = Field(description="Human-readable label rendered in the UI's run-parameters form.")
    type: Literal["int", "float", "bool", "string"] = Field(
        default="string",
        description="Declared type. The framework coerces incoming values against this before the callable runs.",
    )
    required: bool = True
    default: Any = None
    description: str = ""


class ReportDef(BaseModel):
    """One report — a python callable plus the metadata the framework needs to
    discover, gate, and dispatch it.

    Reports are scoped by their declaring plugin (``scope = "nomasx1"``) so the
    license gate is simple: when ``licensed=True``, the framework only loads
    the report when ``license.covers(scope)``. Customer-declared reports
    (outside any plugin) typically set ``licensed=False``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-_]*$",
        description="Slug — unique within the scope. URL path segment, so kebab-case.",
    )
    scope: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-_]*$",
        description="Usually the plugin name. License gating reads this; e.g. 'nomasx1' → covers('nomasx1').",
    )
    title: str = Field(description="Headline shown in the reports list and as the PDF cover title.")
    description: str = ""
    formats: tuple[OutputFormat, ...] = Field(
        default=("pdf", "markdown"),
        min_length=1,
        description="Output formats the report supports. The web layer rejects requests for any other format.",
    )
    params: tuple[ReportParam, ...] = Field(default_factory=tuple)
    callable: str = Field(
        pattern=r"^[a-zA-Z_][\w.]*:[a-zA-Z_]\w*$",
        description="Dotted ``module.path:function`` reference. Resolved once at registry build time.",
    )
    licensed: bool = Field(
        default=True,
        description="When True, the registry only loads this report when the install's license covers ``scope``.",
    )

    @field_validator("params")
    @classmethod
    def _unique_param_names(cls, v: tuple[ReportParam, ...]) -> tuple[ReportParam, ...]:
        names = [p.name for p in v]
        if len(set(names)) != len(names):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate param name(s): {duplicates}")
        return v


@dataclass(frozen=True, slots=True)
class ReportContent:
    """What every report callable returns. The framework renders this to the
    requested output format via :func:`liberty.reports.render.render_content`.

    * ``markdown`` — the body. Headings drive the PDF's TOC entries.
    * ``landscape_svg`` — optional SVG inserted on its own landscape page when
      rendering PDF (matched against the ``![*.svg](*)`` marker the report
      embeds in its markdown — see :func:`liberty.reports.render.build_pdf`).
    * ``title`` — defaults to ``ReportDef.title`` when empty; appears on the
      PDF cover.
    * ``filename_base`` — basename for the ``Content-Disposition`` header.
      ``.md`` / ``.pdf`` suffix is appended by the framework. Sanitised by
      the web layer before being sent to the client.
    * ``pdf_options`` — passed through to ``BuildOptions`` for cover meta,
      colours, header / footer. The framework fills sensible defaults from
      ``settings.app.name`` and ``ReportDef`` first; this overrides per-call.
    """

    markdown: str
    landscape_svg: str | None = None
    title: str = ""
    filename_base: str = "report"
    pdf_options: dict[str, Any] = field(default_factory=dict)
