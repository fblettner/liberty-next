"""The choices of a job's user parameter (``UserParam``): fixed ``choices``, an app's Explorer
models (``choices_from = "explorer_models:<app>"``) or the rows of a read query
(``"query:<connector>/<query>:<value column>:<label column>"``). Shared by the job pages (the
dropdowns / checkboxes) and the fan-out of a ``for_each`` job, where "all" means every choice."""

from __future__ import annotations

import logging
from typing import Any

from liberty.jobs.schema import UserParam

_log = logging.getLogger(__name__)


async def query_choices(connectors: Any, spec: str) -> list[dict[str, str]]:
    """``<connector>/<query>:<value column>:<label column>`` → ``[{value, label}]`` (columns
    matched without case); [] when the query is missing, writable or fails (logged)."""
    target, _, cols = spec.partition(":")
    connector, _, query = target.partition("/")
    value_col, _, label_col = cols.partition(":")
    if not (connector and query and value_col) or connectors is None:
        return []
    try:
        conn = connectors.sql(connector)
        if conn.get_query(query).writable:
            return []
        res = await conn.execute(query, {})
    except Exception as exc:  # noqa: BLE001 — a choice list never breaks the page
        _log.warning("nomaflow job choices %s: %s", spec, exc)
        return []
    out: list[dict[str, str]] = []
    for row in res.rows:
        low = {str(k).lower(): v for k, v in row.items()}
        v = low.get(value_col.lower())
        if v is None:
            continue
        val = str(v).strip()
        lab = low.get((label_col or value_col).lower())
        out.append({"value": val, "label": f"{lab} ({val})" if lab not in (None, "") and str(lab) != str(v) else val})
    return out


async def param_choices(p: UserParam, *, connectors: Any, explorer: Any = None, cache: dict | None = None) -> list[dict[str, str]]:
    """Every choice of *p*, resolved. *cache* shares query results between params / jobs."""
    cache = {} if cache is None else cache
    choices: list[dict[str, str]] = [{"value": c, "label": c} for c in p.choices]
    if p.choices_from:
        kind, _, arg = p.choices_from.partition(":")
        if kind == "explorer_models":
            for app, mid, m in (explorer.items() if explorer is not None else []):
                if app == arg:
                    choices.append({"value": mid, "label": m.label or mid})
        elif kind == "query":
            if arg not in cache:
                cache[arg] = await query_choices(connectors, arg)
            choices.extend(cache[arg])
    return choices
