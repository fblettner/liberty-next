"""Tools that put the Data Explorer in the assistant's hands — totals, rankings, trends and
comparisons without a hand-written aggregate query.

* ``explorer_models`` — the models (per app): hub, entities, what they measure.
* ``explorer_fields`` — a model's fields (dimensions / measures, type, label) — the keys the
  query takes.
* ``explorer_query`` — one aggregate: rows (dimensions, optionally bucketed by month / quarter /
  year), measures (sum / avg / min / max / count / count_distinct), filters, a period, a sort, a
  Top N — the same engine as the Explorer page, under the caller's row-level scope.
* ``explorer_saved`` / ``explorer_save_analysis`` / ``explorer_save_dashboard`` — the user's
  saved analyses and dashboards of a model, and saving a new one on their behalf (the analysis
  definition is run once first, so a wrong field never gets saved; private to the user unless
  ``shared``).

Everything reads ``app.state`` at call time through the ``state`` callable, so a reload is seen;
nothing writes.
"""
from __future__ import annotations

from typing import Any, Callable

from liberty.ai.tools import Tool, tool


def build_explorer_tools(state: Callable[[], Any], *, allowed: list[str] | None = None) -> list[Tool]:
    allow = set(allowed or [])

    def _visible(app: str) -> bool:
        return not allow or app in allow

    def _model(app: str, model: str) -> Any:
        explorer = getattr(state(), "explorer", None)
        m = explorer.get(app, model) if explorer is not None else None
        if m is None or not _visible(app):
            raise ValueError(f"no explorer model {app}/{model} (call explorer_models)")
        return m

    async def _view(app: str, model: str) -> Any:
        st = state()
        m = _model(app, model)
        return await st.explorer_cache.get(st.connectors, getattr(st, "screens", None), app, m, language=None)

    def explorer_models(app: str | None = None) -> dict[str, Any]:
        """List the Data Explorer models — the analytical views built on the applications' data
        (a hub entity, the entities around it, their measures). Call it before explorer_query,
        and whenever the user asks for a total, a ranking, a trend, a share, a comparison by
        period: a model answers those; the raw row queries of list_connectors do not.

        Args:
            app: restrict to one application (connector name).
        """
        explorer = getattr(state(), "explorer", None)
        out = []
        for a, mid, m in (explorer.items() if explorer is not None else []):
            if not _visible(a) or (app and a != app):
                continue
            out.append({
                "app": a, "model": mid, "label": m.label or mid, "description": m.description, "hub": m.hub,
                "entities": [{"id": eid, "label": e.label or eid, "cardinality": e.cardinality, "measures": sorted(e.measures)} for eid, e in m.entities.items()],
                "analyses": [{"id": aid, "label": a_.label} for aid, a_ in m.analyses.items()],
            })
        return {"models": out}

    async def explorer_fields(app: str, model: str, entity: str | None = None) -> dict[str, Any]:
        """The fields of an Explorer model: the keys (``entity.COLUMN``) explorer_query takes,
        each with its label, type (string / number / date / datetime / boolean) and kind
        (dimension or measure). Call it once per model before querying it.

        Args:
            app: the application (from explorer_models).
            model: the model id.
            entity: one entity only (its id); default: every entity.
        """
        view = await _view(app, model)
        out = []
        for ev in view.entities:
            if entity and ev.entity.id != entity:
                continue
            fields = [{"key": f.key, "label": f.label, "type": f.type, "kind": f.kind, **({"aggregate": f.aggregate} if f.aggregate else {})} for f in ev.fields if not f.hidden]
            out.append({"entity": ev.entity.id, "label": ev.entity.label or ev.entity.id, "cardinality": ev.entity.cardinality, "default_date": ev.entity.default_date, "fields": fields, **({"error": ev.error} if ev.error else {})})
        return {"app": app, "model": model, "hub": view.model.hub, "entities": out, "warnings": list(view.warnings)}

    async def explorer_query(
        app: str, model: str, rows: list[dict[str, Any]] | None = None, measures: list[dict[str, Any]] | None = None,
        filters: dict[str, Any] | None = None, period: dict[str, Any] | None = None, sort: list[dict[str, Any]] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run one aggregate on an Explorer model: a total, a ranking, a trend, a comparison.
        Field keys come from explorer_fields. Returns the columns and the grouped rows
        (``groups`` = rows before the Top N cut, ``scanned`` = source rows read).

        Args:
            app: the application (from explorer_models).
            model: the model id.
            rows: the dimensions, e.g. [{"field": "company.BUSINESS_UNIT_NAME"}]; a date field may take "bucket": "month" | "quarter" | "year" | "week" | "day".
            measures: e.g. [{"field": "sales.REVENUE", "agg": "sum"}]; agg = sum | avg | min | max | count | count_distinct; no field = count(*).
            filters: {"entity.FIELD": value} (equals; a list = IN), "entity.FIELD__op": "contains" | "gt" | "lt" | ..., "entity.FIELD__from" / "__to" for a range.
            period: {"field": "sales.PERIOD_DATE", "from": "2026-01-01", "to": "2026-03-31"} — a date window on one field.
            sort: [{"key": "sum(sales.REVENUE)", "dir": "desc"}]; the key of a measure is "<agg>(<field>)", of a row its field key.
            limit: Top N groups (default 100, max 10000).
        """
        from liberty.auth.scope import current_scope
        from liberty.explorer.engine import AggregateRequest, run_aggregate
        st = state()
        view = await _view(app, model)
        # the engine sorts on result columns only: a row's key (``field`` or ``field:bucket``) or a
        # measure's (``agg(field)`` / ``count(*)``) — anything else is dropped and reported, not an error
        keys = {(f"{r['field']}:{r['bucket']}" if r.get("bucket") else r["field"]) for r in rows or [] if r.get("field")}
        keys |= {(f"{m.get('agg', 'sum')}({m['field']})" if m.get("field") else "count(*)") for m in measures or []}
        warnings: list[str] = []
        kept_sort = []
        for s_ in sort or []:
            if s_.get("key") in keys:
                kept_sort.append(s_)
            else:
                warnings.append(f"sort on {s_.get('key')!r} ignored: not a row or a measure of this query (keys: {sorted(keys)})")
        req = AggregateRequest.model_validate({
            "rows": rows or [], "measures": measures or [], "filters": filters or {}, "sort": kept_sort,
            "limit": max(1, min(int(limit or 100), 10000)), **({"period": period} if period else {}),
        })
        res = await run_aggregate(st.connectors, getattr(st, "screens", None), view, req, scope=current_scope.get(), debug=False)
        out = {
            "columns": [{"key": c["key"], "label": c.get("label") or c["key"], "kind": c["kind"], **({"aggregate": c["aggregate"]} if c.get("aggregate") else {})} for c in res.get("columns", [])],
            "rows": res.get("rows", []), "groups": res.get("groups"), "scanned": res.get("scanned"), "truncated": res.get("truncated", False),
        }
        if warnings:
            out["warnings"] = warnings
        return out

    def _store() -> Any:
        from liberty.explorer.store import AnalysisStore
        views = getattr(state(), "user_views", None)
        if views is None:
            raise ValueError("saved analyses are not available (no user-views store)")
        return AnalysisStore(views)

    def _username() -> str:
        from liberty.auth.scope import current_username
        name = current_username.get()
        if not name:
            raise ValueError("no user to save for (not inside a request)")
        return name

    async def explorer_saved(app: str, model: str) -> dict[str, Any]:
        """The saved analyses and dashboards of an Explorer model the user can see (shared ones
        and their own): ids, names, descriptions. An analysis id is what a dashboard tile names.

        Args:
            app: the application.
            model: the model id.
        """
        from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD
        _model(app, model)
        store, user = _store(), _username()
        out: dict[str, Any] = {}
        for kind, key in ((KIND_ANALYSIS, "analyses"), (KIND_DASHBOARD, "dashboards")):
            items = await store.list(f"{app}/{model}", kind, user)
            out[key] = [{"id": i["id"], "name": i["name"], "description": i.get("description"), "shared": i.get("shared"), "owner": i.get("owner")} for i in items]
        return out

    async def explorer_save_analysis(
        app: str, model: str, name: str, definition: dict[str, Any], description: str | None = None, shared: bool = False,
    ) -> dict[str, Any]:
        """Save an analysis on an Explorer model for the user — it then appears in the Data
        Explorer's saved analyses (and can be put on a dashboard). The definition is RUN once
        before saving, so a wrong field is refused rather than stored. Build it from
        explorer_fields: {"entity": "<hub or entity id>", "view": "table" | "pivot" | "bar" |
        "line" | "pie" | "kpi", "rows": [{"field": "entity.FIELD", "bucket": "month"?}],
        "measures": [{"field": "entity.FIELD" | null, "agg": "sum" | "avg" | "min" | "max" | "count" | "count_distinct"}],
        "filterRows": [{"field": "entity.FIELD", "op": "equals" | "contains" | "in" | "gt" | "lt" | "between" | "empty" | "notEmpty", "value": "…", "valueTo": "…"?}],
        "period": {"field": "entity.DATE_FIELD", "preset": "all" | "thisMonth" | "lastMonth" | "thisYear" | "last12" | "last30" | "custom", "range": {"dateFrom": "", "dateTo": ""}} | null,
        "sort": [{"key": "sum(entity.FIELD)", "dir": "desc"}], "limit": 100}. Ask the user which
        model and what they want to see (rows, figures, period) when it is not clear.

        Args:
            app: the application.
            model: the model id.
            name: the analysis name (what the user will look for).
            definition: the analysis definition (see above).
            description: one line on what the analysis shows.
            shared: true to share it with every user of the model (default: private to the user).
        """
        from liberty.auth.scope import current_scope
        from liberty.explorer.engine import run_aggregate
        from liberty.explorer.report import definition_to_aggregate
        from liberty.explorer.store import KIND_ANALYSIS
        st = state()
        view = await _view(app, model)
        d = dict(definition or {})
        d.setdefault("entity", view.model.hub); d.setdefault("view", "table")
        for k, v in (("rows", []), ("measures", []), ("filterRows", []), ("sort", []), ("calculated", []), ("period", None)):
            d.setdefault(k, v)
        d["limit"] = max(1, min(int(d.get("limit") or 100), 10000))
        if d["view"] != "detail":
            req = definition_to_aggregate(d)
            res = await run_aggregate(st.connectors, getattr(st, "screens", None), view, req, scope=current_scope.get(), debug=False)
            preview = {"groups": res.get("groups"), "columns": [c["key"] for c in res.get("columns", [])], "first_rows": res.get("rows", [])[:3]}
        else:
            preview = {}
        rec = await _store().save(f"{app}/{model}", KIND_ANALYSIS, _username(), obj_id=None, name=name, definition=d, description=description, shared=shared)
        return {"saved": {"id": rec["id"], "name": rec["name"], "shared": rec["shared"]}, "preview": preview,
                "where": f"Data Explorer → {app} / {model} → saved analyses"}

    async def explorer_save_dashboard(
        app: str, model: str, name: str, tiles: list[dict[str, Any]], description: str | None = None, period: str | None = None, shared: bool = False,
    ) -> dict[str, Any]:
        """Save a dashboard of saved analyses on an Explorer model for the user (Data Explorer →
        Dashboards). Each tile names a saved analysis (explorer_saved, or one you just saved with
        explorer_save_analysis): {"analysis": "<id>", "w": 1-4 (of 4 columns), "h": "s" | "m" | "l", "title": "…"?}.

        Args:
            app: the application.
            model: the model id.
            name: the dashboard name.
            tiles: the tiles, in display order.
            description: one line on what it shows.
            period: a period preset applied to every tile ("thisMonth", "lastMonth", "thisYear", "last12", "last30", "all"); blank = each analysis keeps its own.
            shared: true to share it with every user of the model.
        """
        from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD
        _model(app, model)
        store, user = _store(), _username()
        known = {i["id"] for i in await store.list(f"{app}/{model}", KIND_ANALYSIS, user)}
        clean = []
        for t_ in tiles or []:
            aid = str(t_.get("analysis") or "")
            if aid not in known:
                raise ValueError(f"unknown analysis {aid!r} (call explorer_saved; known: {sorted(known)})")
            w = int(t_.get("w") or 2); h = str(t_.get("h") or "m")
            clean.append({"analysis": aid, "w": max(1, min(w, 4)), "h": h if h in ("s", "m", "l") else "m", **({"title": str(t_["title"])} if t_.get("title") else {})})
        if not clean:
            raise ValueError("a dashboard needs at least one tile")
        definition = {"period": {"preset": period, "range": {"dateFrom": "", "dateTo": ""}} if period else None, "tiles": clean}
        rec = await store.save(f"{app}/{model}", KIND_DASHBOARD, user, obj_id=None, name=name, definition=definition, description=description, shared=shared)
        return {"saved": {"id": rec["id"], "name": rec["name"], "shared": rec["shared"]}, "where": f"Data Explorer → {app} / {model} → dashboards"}

    return [
        tool(explorer_models, summary_keys=("app",)),
        tool(explorer_fields, summary_keys=("app", "model")),
        tool(explorer_query, summary_keys=("app", "model")),
        tool(explorer_saved, summary_keys=("app", "model")),
        tool(explorer_save_analysis, summary_keys=("app", "model", "name")),
        tool(explorer_save_dashboard, summary_keys=("app", "model", "name")),
    ]
