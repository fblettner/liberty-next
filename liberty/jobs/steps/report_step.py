"""``report`` step — render a saved explorer analysis / dashboard or a plugin report to a file
in the run's work area (C5, the digest). The file is picked up by a ``notify`` step, and the
Run detail offers it for download like a rejects report.

Visibility: with ``as_user`` the rendering runs under that user's row-level scope (their roles'
rules with their attributes — a "filiale" digest only contains that company); with ``as_role``
under the role's literal rules (a rule that takes its values from a user attribute allows
nothing, so prefer ``as_user`` then). Neither: unrestricted.
"""

from __future__ import annotations

import inspect
import logging
from datetime import date
from typing import Any, Callable

from liberty.auth.scope import DataScope, ScopeRule, resolve_scope
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)


class ReportStepExecutor:
    """``state`` returns the live ``app.state`` (connectors, screens, explorer, explorer_cache,
    user_views, reports, settings, auth) — read at run time so a reload is seen."""

    def __init__(self, state: Callable[[], Any]) -> None:
        self._state = state

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.REPORT:
            raise StepFailed(f"ReportStepExecutor cannot run a {step.type.value} step")
        st = self._state()
        settings = getattr(st, "settings", None)
        if settings is None:
            raise StepFailed("report: the app settings are unavailable")
        kind, _, rest = (step.report or "").partition(":")
        fmt = step.format or "xlsx"
        window = self._window(step)
        scope = await self._scope(st, step)
        if kind in ("analysis", "dashboard"):
            data, title, sections = await self._explorer(st, step, kind, rest, fmt, window, scope)
            log = f"{kind} {rest}: {len(sections)} section(s)"
        else:
            data, title = await self._plugin_report(st, step, rest, fmt)
            log = f"report {rest}"
        from liberty.jobs.files import job_dir, safe_name
        base = safe_name(step.filename or title or rest.replace(":", "-")) or "report"
        if not base.lower().endswith(f".{fmt}"):
            base = f"{base}.{fmt}"
        d = job_dir(settings, ctx.job_id)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{ctx.run_id}-{base}"
        out.write_bytes(data)
        _log.info("nomaflow.report run=%s step=%s wrote %s (%d bytes)", ctx.run_id, step.name, out.name, len(data))
        return StepResult(rows_affected=1, log_excerpt=f"{log} → {out.name} ({len(data)} bytes)", extras={"file": out.name, "format": fmt, "title": title})

    # ── pieces ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _window(step: Step) -> tuple[date, date] | None:
        if step.window_from and step.window_to:
            return date.fromisoformat(step.window_from), date.fromisoformat(step.window_to)
        if step.period:
            from liberty.explorer.report import period_range
            r = period_range(step.period)
            if r is None and step.period != "all":
                raise StepFailed(f"report: unknown period preset {step.period!r}")
            return r
        return None

    @staticmethod
    async def _scope(st: Any, step: Step) -> DataScope | None:
        backend = getattr(st, "auth_backend", None) or getattr(st, "auth", None)
        if step.as_user:
            if backend is None:
                raise StepFailed("report: as_user needs the auth backend")
            user = await backend.get_by_username(step.as_user)
            if user is None:
                raise StepFailed(f"report: unknown user {step.as_user!r}")
            if getattr(user, "is_superuser", False):
                return None
            roles = {name: scope for name, _perms, _desc, scope in await backend.list_roles()}
            rules = [[ScopeRule(**r) for r in (roles.get(role) or [])] for role in (getattr(user, "roles", None) or [])]
            claims = resolve_scope(rules, getattr(user, "attributes", None) or {})
            return DataScope.from_claims(claims)
        if step.as_role:
            if backend is None:
                raise StepFailed("report: as_role needs the auth backend")
            roles = {name: scope for name, _perms, _desc, scope in await backend.list_roles()}
            if step.as_role not in roles:
                raise StepFailed(f"report: unknown role {step.as_role!r}")
            rules = [ScopeRule(**r) for r in (roles[step.as_role] or [])]
            return DataScope.from_claims(resolve_scope([rules], {}))
        return None

    async def _explorer(
        self, st: Any, step: Step, kind: str, obj_id: str, fmt: str, window: tuple[date, date] | None, scope: DataScope | None,
    ) -> tuple[bytes, str, list[Any]]:
        from liberty.explorer.report import build_sections, common_period, sections_to_pdf, sections_to_xlsx
        from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD, AnalysisStore
        explorer = getattr(st, "explorer", None)
        cache = getattr(st, "explorer_cache", None)
        views = getattr(st, "user_views", None)
        if explorer is None or cache is None or views is None:
            raise StepFailed("report: the explorer is unavailable")
        model = explorer.get(step.app or "", step.model or "")
        if model is None:
            raise StepFailed(f"report: no explorer model {step.app}/{step.model}")
        connectors = st.connectors
        screens = getattr(st, "screens", None)
        view = await cache.get(connectors, screens, step.app or "", model, language=None)
        store = AnalysisStore(views)
        key = f"{step.app}/{step.model}"
        items: list[tuple[str, dict[str, Any]]] = []
        if kind == "analysis":
            a = await store.get(key, KIND_ANALYSIS, "", obj_id)
            if a is None:
                raise StepFailed(f"report: no shared analysis {obj_id!r} in {key}")
            title = a["name"]
            items.append((a["name"], a["definition"]))
        else:
            d = await store.get(key, KIND_DASHBOARD, "", obj_id)
            if d is None:
                raise StepFailed(f"report: no shared dashboard {obj_id!r} in {key}")
            title = d["name"]
            analyses = {a["id"]: a for a in await store.list(key, KIND_ANALYSIS, "")}
            period = (d["definition"] or {}).get("period")
            for tile in (d["definition"] or {}).get("tiles") or []:
                a = analyses.get(tile.get("analysis"))
                if a is None:
                    continue
                definition = dict(a["definition"])
                if period and (definition.get("period") or {}).get("field"):
                    definition["period"] = {**definition["period"], "preset": period.get("preset"), "range": period.get("range") or {}}
                items.append((tile.get("title") or a["name"], definition))
        sections = await build_sections(connectors, screens, view, items, window=window, scope=scope)
        if fmt == "pdf":
            settings = st.settings
            branding = getattr(getattr(settings, "reports", None), "branding", None)
            data = sections_to_pdf(
                sections, title, app_name=getattr(settings.app, "name", ""), branding=branding.model_dump() if branding is not None else None,
                period=common_period(items, window),
            )
        else:
            data = sections_to_xlsx(sections)
        return data, title, sections

    async def _plugin_report(self, st: Any, step: Step, ref: str, fmt: str) -> tuple[bytes, str]:
        from liberty.reports.render import render_content
        from liberty.reports.schema import ReportContent
        registry = getattr(st, "reports", None)
        if registry is None:
            raise StepFailed("report: the reports registry is unavailable")
        scope, _, report_id = ref.partition(":")
        try:
            d = registry.get(scope, report_id)
        except Exception as exc:  # noqa: BLE001
            raise StepFailed(f"report: {exc}") from exc
        if fmt not in d.formats:
            raise StepFailed(f"report: {ref} does not render {fmt} (supported: {list(d.formats)})")
        fn = registry.get_callable(scope, report_id)
        # the step's ``params``, then what the run carries in ``op_kwargs`` (the job-level
        # ``params``, a per-run override, a user parameter from the job's page) — later wins;
        # typed by the report's declared parameters (a user parameter arrives as text)
        kwargs: dict[str, Any] = {**dict(step.params), **dict(step.op_kwargs)}
        for p in getattr(d, "params", ()) or ():
            v = kwargs.get(p.name)
            if isinstance(v, str) and getattr(p, "type", None) in ("int", "float"):
                v = v.strip()
                if v == "":
                    kwargs.pop(p.name)
                else:
                    try:
                        kwargs[p.name] = int(v) if p.type == "int" else float(v)
                    except ValueError as exc:
                        raise StepFailed(f"report: parameter {p.name!r} must be a number, got {v!r}") from exc
        try:
            sig = inspect.signature(fn, eval_str=True)
            names = sig.parameters
            var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in names.values())
        except (TypeError, ValueError, NameError):  # pragma: no cover
            names, var_kw = {}, True
        for name, value in (("connectors", getattr(st, "connectors", None)), ("settings", getattr(st, "settings", None))):
            if var_kw or name in names:
                kwargs.setdefault(name, value)
        raw = fn(**kwargs)
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, ReportContent):
            raise StepFailed(f"report: {ref} returned {type(raw).__name__}, expected ReportContent")
        settings = st.settings
        branding = getattr(getattr(settings, "reports", None), "branding", None)
        body, _ct, _fn = render_content(raw, fmt if fmt == "pdf" else "markdown", app_name=getattr(settings.app, "name", ""), report_title=d.title, branding=branding.model_dump() if branding is not None else None)
        if fmt == "xlsx":
            raise StepFailed("report: a plugin report renders pdf (or markdown), not xlsx")
        return body, raw.title or d.title
