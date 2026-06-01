"""``/api/reports`` — the public report endpoints.

Three routes:

* ``GET  /api/reports`` — list every report visible to the caller, optionally
  filtered by ``?scope=``. License + per-report permission gate apply.
* ``GET  /api/reports/{scope}/{id}`` — one report's metadata (params + formats).
* ``POST /api/reports/{scope}/{id}/run`` — dispatch the report's callable and
  stream the result as the requested format (``"pdf"`` or ``"markdown"``).

The callable's kwargs come from three sources, in order of precedence:

1. ``body.params`` — operator-provided, coerced against the param spec.
2. Injected by name when the callable declares one of ``connectors``,
   ``settings``, ``request`` — same convention as
   :class:`~liberty.jobs.steps.python_step.PythonStepExecutor`.
3. Defaults from :class:`~liberty.reports.ReportDef.params`.

Errors are mapped explicitly:

* 404 — unknown ``scope:id`` (or unlicensed plugin, since gated reports never
  enter the registry).
* 422 — required param missing, value can't be coerced, unsupported format.
* 500 — the callable raised; details logged with traceback, only a short
  ``{"detail": ...}`` returned to the caller.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from liberty.auth.dependencies import CurrentPrincipal
from liberty.coercion import CoercionError, coerce_to_annotation
from liberty.reports.schema import (
    OutputFormat,
    ReportContent,
    ReportDef,
    UnknownReportError,
)
from liberty.reports.render import render_content

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


# --------------------------------------------------------------------------- #
# Permission helper
# --------------------------------------------------------------------------- #
def _permission_for(scope: str, report_id: str) -> str:
    """Permission string for ``POST /run`` on a given report.

    Simpler-is-better per the design call: one wildcard slot
    (``reports:{scope}:*:run``) is granted at the role level — anybody with
    the role can run any report in that scope. Per-report fine-grain
    permissions can land later by tightening this helper without changing
    the call site.
    """
    del report_id  # reserved for a future per-report permission tier
    return f"reports:{scope}:*:run"


def _require_run_permission(principal, scope: str, report_id: str) -> None:
    perm = _permission_for(scope, report_id)
    if not principal.has_permission(perm):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class RunReportBody(BaseModel):
    """``POST /api/reports/{scope}/{id}/run`` request body."""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(default_factory=dict, description="Report parameters keyed by ReportParam.name.")
    format: OutputFormat = Field(default="pdf", description="Requested output format.")


class ReportListResponse(BaseModel):
    reports: list[ReportDef]


# --------------------------------------------------------------------------- #
# Routes — list / metadata / run
# --------------------------------------------------------------------------- #
@router.get("", summary="List reports", response_model=ReportListResponse)
async def list_reports(
    request: Request,
    principal: CurrentPrincipal,
    scope: Optional[str] = None,
) -> ReportListResponse:
    """Every report the caller has run-permission for. Optionally filtered by
    ``scope``. License-gated reports never enter the registry so they're
    naturally absent here."""
    registry = request.app.state.reports
    visible = [
        d for d in registry.list_for(scope)
        if principal.has_permission(_permission_for(d.scope, d.id))
    ]
    return ReportListResponse(reports=visible)


@router.get("/{scope}/{report_id}", summary="Report metadata", response_model=ReportDef)
async def get_report(
    scope: str,
    report_id: str,
    request: Request,
    principal: CurrentPrincipal,
) -> ReportDef:
    """One report's declaration — title, description, params, formats. Use this
    to build the run-parameters form."""
    registry = request.app.state.reports
    try:
        d = registry.get(scope, report_id)
    except UnknownReportError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _require_run_permission(principal, scope, report_id)
    return d


@router.post("/{scope}/{report_id}/run", summary="Run a report")
async def run_report(
    scope: str,
    report_id: str,
    body: RunReportBody,
    request: Request,
    principal: CurrentPrincipal,
):
    """Dispatch the report's callable and stream the result.

    Validates: report exists (404), caller is permitted (403), format is
    supported (422), required params are present (422), each param coerces
    to its declared type (422). On success, returns the rendered file with a
    correct ``Content-Disposition`` so the browser triggers a download.
    """
    registry = request.app.state.reports
    try:
        d: ReportDef = registry.get(scope, report_id)
    except UnknownReportError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _require_run_permission(principal, scope, report_id)

    if body.format not in d.formats:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"format {body.format!r} not supported by {scope}:{report_id} "
                   f"(supported: {list(d.formats)})",
        )

    # Validate + coerce params against the ReportParam spec. ``string`` is the
    # widest type — anything else uses the same coercion helper jobs use.
    type_map = {"int": int, "float": float, "bool": bool, "string": str}
    params: dict[str, Any] = {}
    for spec in d.params:
        if spec.name in body.params:
            raw = body.params[spec.name]
        elif spec.default is not None:
            raw = spec.default
        elif spec.required:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"missing required param {spec.name!r} for {scope}:{report_id}",
            )
        else:
            continue
        try:
            params[spec.name] = coerce_to_annotation(raw, type_map[spec.type])
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"params[{spec.name!r}]={raw!r} cannot be coerced to "
                       f"{spec.type}: {exc}",
            ) from exc

    # Reject extra keys the report didn't declare — protects against typos
    # silently going into **kwargs.
    declared = {p.name for p in d.params}
    extras = set(body.params) - declared
    if extras:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown param(s) {sorted(extras)} for {scope}:{report_id}",
        )

    # Build the call kwargs — operator params plus injections (connectors /
    # settings / request) only when the callable declares them by name.
    fn = registry.get_callable(scope, report_id)
    kwargs = dict(params)
    try:
        sig = inspect.signature(fn, eval_str=True)
    except (TypeError, ValueError, NameError):  # pragma: no cover — builtins
        sig = None
    if sig is not None:
        sig_params = sig.parameters
        accepts_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig_params.values()
        )
        injections = [
            ("connectors", getattr(request.app.state, "connectors", None)),
            ("settings", getattr(request.app.state, "settings", None)),
            ("request", request),
            ("principal", principal),
        ]
        for name, value in injections:
            if accepts_kwargs or name in sig_params:
                kwargs.setdefault(name, value)

    # Run the callable. The signature can be sync or async.
    try:
        raw = fn(**kwargs)
        if inspect.isawaitable(raw):
            raw = await raw
    except CoercionError as exc:
        # Defensive — the callable shouldn't re-coerce, but if it does we'd
        # rather a clean 422 than a stack trace.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"params[{exc.key!r}]={exc.value!r}: {exc.cause}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface the failure cleanly
        _log.exception(
            "reports: run %s:%s by %s failed",
            scope, report_id, principal.username,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"report failed: {type(exc).__name__}: {exc}",
        ) from exc

    if not isinstance(raw, ReportContent):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"report {scope}:{report_id} returned {type(raw).__name__}, "
                   f"expected ReportContent",
        )

    # Render in the requested format. Use the install's app name as the cover
    # default (per the design call); ReportContent.pdf_options overrides per-call.
    settings = request.app.state.settings
    body_bytes, content_type, filename = render_content(
        raw,
        body.format,
        app_name=getattr(settings.app, "name", ""),
        report_title=d.title,
    )

    safe_filename = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in filename
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
    }
    return StreamingResponse(
        iter([body_bytes]),
        media_type=content_type,
        headers=headers,
    )
