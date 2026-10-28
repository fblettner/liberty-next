"""Tools that put the Data Explorer in the assistant's hands — totals, rankings, trends and
comparisons without a hand-written aggregate query.

* ``explorer_models`` — the models (per app): hub, entities, what they measure.
* ``explorer_fields`` — a model's fields (dimensions / measures, type, label) — the keys the
  query takes.
* ``explorer_query`` — one aggregate: rows (dimensions, optionally bucketed by month / quarter /
  year), measures (sum / avg / min / max / count / count_distinct), filters, a period, a sort, a
  Top N — the same engine as the Explorer page, under the caller's row-level scope.

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
        req = AggregateRequest.model_validate({
            "rows": rows or [], "measures": measures or [], "filters": filters or {}, "sort": sort or [],
            "limit": max(1, min(int(limit or 100), 10000)), **({"period": period} if period else {}),
        })
        res = await run_aggregate(st.connectors, getattr(st, "screens", None), view, req, scope=current_scope.get(), debug=False)
        return {
            "columns": [{"key": c["key"], "label": c.get("label") or c["key"], "kind": c["kind"], **({"aggregate": c["aggregate"]} if c.get("aggregate") else {})} for c in res.get("columns", [])],
            "rows": res.get("rows", []), "groups": res.get("groups"), "scanned": res.get("scanned"), "truncated": res.get("truncated", False),
        }

    return [
        tool(explorer_models, summary_keys=("app",)),
        tool(explorer_fields, summary_keys=("app", "model")),
        tool(explorer_query, summary_keys=("app", "model")),
    ]
