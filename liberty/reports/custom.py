"""Operator-authored ("custom") reports — declared in ``reports.toml`` rather
than in a plugin's Python.

A custom report is a TOML entry that wires:

* identity (id, title, description, formats, params) — same shape as
  plugin-declared :class:`~liberty.reports.schema.ReportDef`;
* a **data binding** — a named query on a connector that the framework runs
  with the operator's params and feeds to the template as a list of rows;
* an **inline Jinja markdown template** — rendered with a sandboxed
  environment (no Python builtins, no dunder access) against a context
  ``ctx = {data: {rows, columns}, params, app_name, audit_date}``;
* optional ``pdf_options`` — overrides the operator's install-wide branding
  (cf. :class:`liberty.config.ReportsBrandingSettings`) for THIS report.

At startup :func:`build_custom_report_entries` reads the TOML, builds one
``(ReportDef, callable)`` pair per template, and the wiring layer merges them
into the framework's :class:`~liberty.reports.registry.ReportRegistry`
alongside plugin reports. The dispatcher in :mod:`liberty.web.reports` sees a
custom report no differently — same ``/run`` endpoint, same permission gate,
same blob streaming.

Permission model: every custom report carries ``scope = "custom"`` so a single
``reports:custom:*:run`` role grant unlocks every operator-authored report.
The id stays per-report so collisions surface as registry-build errors at
startup (not at first request).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

import tomllib
from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, field_validator

from liberty.reports.schema import OutputFormat, ReportContent, ReportDef, ReportParam

_log = logging.getLogger(__name__)


# The single scope every operator-authored report lives under. Wildcard per
# Phase 4 design call — keeps the permission story simple ("anyone with
# ``reports:custom:*:run`` can run any custom report") without precluding a
# fine-grained tier later.
CUSTOM_SCOPE = "custom"


# --------------------------------------------------------------------------- #
# Schema — what an operator declares in reports.toml
# --------------------------------------------------------------------------- #
class CustomReportDataBinding(BaseModel):
    """Data source for a custom report — a named query on a configured
    connector. Operator params are passed through to the query (only the names
    listed in ``params`` are forwarded; extras are silently ignored).

    Example::

        [reports.audit-summary.data]
        connector = "nomasx1"
        query = "license_financial_get"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector: str = Field(
        pattern=r"^[a-zA-Z0-9_][\w-]*$",
        description="Connector name as declared in connectors.toml.",
    )
    query: str = Field(
        pattern=r"^[a-zA-Z0-9_][\w-]*$",
        description="Named query — must exist on the connector at run time.",
    )


class CustomReportTemplate(BaseModel):
    """One operator-authored report. Persisted as ``[reports.<id>]`` in
    ``reports.toml``; round-tripped via ``GET/PUT /admin/config/reports/parsed``.

    The ``id`` is also the TOML key, so it has the same kebab-case slug shape
    the framework's :class:`ReportDef` requires (and that the URL path uses).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-_]*$",
        description="Slug — unique across all custom reports. URL path segment.",
    )
    title: str = Field(description="Headline shown in the reports list + PDF cover.")
    description: str = ""
    formats: tuple[OutputFormat, ...] = Field(
        default=("pdf", "markdown"),
        min_length=1,
    )
    params: tuple[ReportParam, ...] = Field(default_factory=tuple)
    data: CustomReportDataBinding
    template_inline: str = Field(
        description=(
            "Jinja markdown template. Rendered in a sandboxed environment "
            "against ``ctx = {data: {rows, columns}, params, …}``."
        ),
    )
    pdf_options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-report PDF overrides. Sits ABOVE Settings → Reports → Branding "
            "in precedence — anything set here overrides install-wide defaults "
            "for THIS report only. See BuildOptions for field names."
        ),
    )

    @field_validator("params")
    @classmethod
    def _unique_param_names(cls, v: tuple[ReportParam, ...]) -> tuple[ReportParam, ...]:
        names = [p.name for p in v]
        if len(set(names)) != len(names):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate param name(s): {duplicates}")
        return v


# --------------------------------------------------------------------------- #
# TOML load / save
# --------------------------------------------------------------------------- #
def load_custom_reports(path: Path | str | None) -> list[CustomReportTemplate]:
    """Read every ``[reports.<id>]`` block from *path*.

    Returns an empty list when *path* is None or the file doesn't exist —
    same convention as every other liberty config loader (a missing file is
    a valid "no entries" state, not an error). Validation errors raise
    ``ValueError`` with the offending id in the message so the operator
    knows which entry to fix."""
    if path is None:
        return []
    p = Path(path)
    if not p.exists():
        return []
    with p.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f) or {}
    reports = data.get("reports") or {}
    if not isinstance(reports, dict):
        raise ValueError(
            f"{p}: top-level [reports] must be a table of {{id: definition}} entries."
        )
    out: list[CustomReportTemplate] = []
    for report_id, body in reports.items():
        if not isinstance(body, dict):
            raise ValueError(
                f"{p}: [reports.{report_id}] must be a table (got {type(body).__name__})."
            )
        payload = {**body, "id": report_id}
        try:
            out.append(CustomReportTemplate.model_validate(payload))
        except Exception as exc:
            raise ValueError(f"{p}: [reports.{report_id}] invalid — {exc}") from exc
    return out


# --------------------------------------------------------------------------- #
# Sandbox — Jinja restricted to safe attribute access
# --------------------------------------------------------------------------- #
# A SandboxedEnvironment blocks Python builtins, attribute access on
# ``__class__`` / ``__mro__`` / ``__subclasses__`` / etc., and unsafe global
# functions. The template can still use the row dicts naturally
# (``{{ row.cpt_id }}``, ``{{ row['cpt_id'] }}``) and standard Jinja filters
# (``length``, ``join``, ``sort``, ``selectattr``). See
# https://jinja.palletsprojects.com/en/latest/sandbox/ for the trust model.
def _make_sandbox_env() -> SandboxedEnvironment:
    """Return a freshly-built :class:`SandboxedEnvironment` configured to
    match how plugin reports use Jinja (StrictUndefined, autoescape off for
    markdown bodies). One env per render — the template source is short
    enough that the parse cost is negligible vs. running the data query."""
    from jinja2 import StrictUndefined
    env = SandboxedEnvironment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,        # markdown body, not HTML
    )
    return env


# --------------------------------------------------------------------------- #
# Generic callable — what the dispatcher invokes for every custom report
# --------------------------------------------------------------------------- #
def make_custom_callable(
    template: CustomReportTemplate,
) -> Callable[..., Awaitable[ReportContent]]:
    """Build the async callable the framework dispatcher invokes for *template*.

    The returned function follows the same convention as plugin callables:
    declares the framework-injected kwargs it needs by name (``connectors``,
    ``settings``), accepts operator-supplied params as ordinary kwargs. The
    dispatcher in :mod:`liberty.web.reports` already handles the injection +
    coercion via the existing infrastructure — we just declare the surface."""

    async def _run(*, connectors: Any, **operator_params: Any) -> ReportContent:
        # Pick the connector + run the named query with the operator's params.
        # The connector's ``execute`` enforces its own permission / writability /
        # row-cap rules — same as when the query runs through /api/sql/…, so the
        # operator can't bypass query-level controls via a custom report.
        connector = connectors.get(template.data.connector)
        # Forward only the params declared on the template — extra payload keys
        # like ``request``, ``principal`` injected by the dispatcher are NOT
        # query params and would confuse the prepared statement.
        forwardable_names = {p.name for p in template.params}
        query_params = {
            k: v for k, v in operator_params.items() if k in forwardable_names
        }
        result = await connector.execute(template.data.query, query_params)

        # Render the template in a sandbox. ``ctx`` exposes:
        #   data.rows     — list of row dicts (dict[str, Any])
        #   data.columns  — list of {name, label, type} dicts (when available)
        #   params        — the operator's input as-passed
        env = _make_sandbox_env()
        try:
            tpl = env.from_string(template.template_inline)
        except TemplateError as exc:
            raise RuntimeError(
                f"custom report {template.id!r}: template parse error — {exc}"
            ) from exc
        columns = [
            {"name": getattr(c, "name", None), "label": getattr(c, "label", None),
             "type": getattr(c, "type", None)}
            for c in (getattr(result, "columns", []) or [])
        ]
        ctx = {
            "data": {"rows": getattr(result, "rows", []) or [], "columns": columns},
            "params": dict(operator_params),
        }
        try:
            body = tpl.render(ctx=ctx)
        except TemplateError as exc:
            raise RuntimeError(
                f"custom report {template.id!r}: template render error — {exc}"
            ) from exc

        return ReportContent(
            markdown=body,
            title=template.title,
            filename_base=template.id,
            pdf_options=dict(template.pdf_options or {}),
        )

    # Give it a meaningful __name__ so traceback / log lines are readable.
    _run.__name__ = f"custom_report__{template.id}"
    return _run


# --------------------------------------------------------------------------- #
# Build report entries — fed into the framework registry
# --------------------------------------------------------------------------- #
def build_custom_report_entries(
    templates: list[CustomReportTemplate],
) -> tuple[list[ReportDef], dict[str, Callable[..., Any]]]:
    """Turn every custom :class:`CustomReportTemplate` into a
    ``(ReportDef, callable)`` pair the framework's
    :class:`~liberty.reports.registry.ReportRegistry` accepts.

    Each ReportDef carries ``scope = "custom"``, ``licensed = False``,
    and a synthetic ``callable`` string of the form
    ``liberty.reports.custom:custom_report__<id>``. The string isn't actually
    resolved at registry-build time — the registry's
    ``_resolve_callable`` path is bypassed because we pre-supply the callable
    in the ``callables`` dict — but it stays unique per report so any future
    introspection (logs, traces) is unambiguous.
    """
    defs: list[ReportDef] = []
    callables: dict[str, Callable[..., Any]] = {}
    for t in templates:
        # ReportDef.callable is regex-validated to ``mod.path:fn_name`` where
        # the function-name part disallows hyphens; the id slug allows them.
        # Sanitise hyphens to underscores so the marker is a valid identifier
        # even when the operator picks an id like "audit-summary".
        safe_id = t.id.replace("-", "_")
        callable_marker = f"liberty.reports.custom:custom_report__{safe_id}"
        defs.append(ReportDef(
            id=t.id,
            scope=CUSTOM_SCOPE,
            title=t.title,
            description=t.description,
            formats=t.formats,
            params=t.params,
            callable=callable_marker,
            licensed=False,        # operator-authored — no license gating
        ))
        callables[f"{CUSTOM_SCOPE}:{t.id}"] = make_custom_callable(t)
    return defs, callables
