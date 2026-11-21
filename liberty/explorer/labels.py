"""What a coded column shows in an export: its label, its code, or both.

The Explorer page lets a field read through an enum or a lookup show its **label** (the default),
its **code**, or **both** — the choice sits on the row of the saved definition (``rows[].show``).
This module is that reading on the server, for the workbook and the PDF: the browser's
``services/lookups.ts`` (how a lookup's rows become code → label) and
``pages/Explorer/useDimText.ts`` (what a cell shows) in Python.

A lookup is read through a **runner** the caller provides (``run(connector, query, params)`` →
the query's result): the export route's checks the caller's permission, the job's does not. A
lookup that cannot be read — no permission, an error — leaves the codes as they are: an export
never fails on a label.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

_log = logging.getLogger("liberty.explorer.labels")

RunQuery = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
LABEL_SUFFIX = "__label"


def show_modes(rows: list[dict[str, Any]] | None) -> dict[str, str]:
    """The choice per result column, by the dimension's key (``field`` or ``field@bucket``) and by
    the plain field (a detail column). Only what differs from the default (the label) is in it."""
    out: dict[str, str] = {}
    for r in rows or []:
        show = r.get("show")
        if show in ("both", "code") and r.get("field"):
            out[f"{r['field']}@{r['bucket']}" if r.get("bucket") else r["field"]] = show
            out[r["field"]] = show
    return out


def shown_text(code: str, label: str | None, show: str) -> str:
    """What a code with its label shows. A code without a label (not in the list) is the code."""
    if show == "code" or not label or label == code:
        return code
    return f"{code} · {label}" if show == "both" else label


def is_coded(column: dict[str, Any]) -> bool:
    """A result column whose values are codes with a label: an enum or a lookup, not a bucketed
    date, not a measure."""
    rule = column.get("rule") or {}
    return column.get("kind") != "measure" and not column.get("bucket") and rule.get("kind") in ("enum", "lookup")


async def rule_labels(rule: dict[str, Any], run: RunQuery) -> dict[str, str]:
    """code → label of an enum or a lookup rule. The codes are trimmed (a CHAR column pads them).

    A lookup's static ``params`` are bound on the query AND narrow its rows on the same-named
    columns, as in the browser: a UDC query returns every code, the system and the type pick the
    list. Several ``sources`` are read one after the other; the first label of a code wins."""
    if rule.get("kind") == "enum":
        return {str(v.get("value")).strip(): str(v.get("label") or v.get("value")) for v in rule.get("values") or [] if v.get("value") is not None}
    params = {k: v for k, v in (rule.get("params") or {}).items() if v not in ("", None)}
    sources = rule.get("sources") or []
    if len(sources) < 2:
        sources = [{"connector": rule.get("connector"), "query": rule.get("query")}]
    value, label = str(rule.get("value") or "").lower(), str(rule.get("label") or "").lower()
    out: dict[str, str] = {}
    for src in sources:
        result = await run(str(src.get("connector")), str(src.get("query")), dict(params))
        for row in result.get("rows") or []:
            low = {str(k).lower(): v for k, v in row.items()}
            if any(k.lower() in low and str(low[k.lower()] if low[k.lower()] is not None else "").strip() != str(v).strip() for k, v in params.items()):
                continue
            code = low.get(value)
            if code is None:
                continue
            text = low.get(label)
            out.setdefault(str(code).strip(), str(code).strip() if text is None else str(text))
    return out


async def apply_shows(
    columns: list[dict[str, Any]], rows: list[dict[str, Any]], definition: dict[str, Any], run: RunQuery | None, *,
    split: bool, memo: dict[str, dict[str, str]] | None = None, id_suffix: str = "(ID)",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """*columns* and *rows* as the definition wants its coded columns shown.

    ``label`` (the default) replaces the code by its label, ``code`` leaves it, ``both`` gives —
    with *split* (a table) — two columns, the code under ``<label> (ID)`` then the label, and
    without it (a pivot, a chart: one cell per value) the text ``code · label``. Without a runner
    nothing changes: the codes, as before."""
    if run is None:
        return columns, rows
    shows = show_modes(definition.get("rows"))
    memo = memo if memo is not None else {}
    out_cols: list[dict[str, Any]] = []
    out_rows = [dict(r) for r in rows]
    for c in columns:
        key = c["key"]
        mode = shows.get(key) or shows.get(c.get("field") or "") or "label"
        if not is_coded(c) or mode == "code":
            out_cols.append(c)
            continue
        rule = c["rule"]
        memo_key = json.dumps(rule, sort_keys=True, default=str)
        if memo_key not in memo:
            try:
                memo[memo_key] = await rule_labels(rule, run)
            except Exception as exc:  # noqa: BLE001 — a label never fails an export: the codes stay
                _log.info("export: the labels of %s could not be read (%s) — the codes are written", key, exc)
                memo[memo_key] = {}
        labels = memo[memo_key]
        if not labels:
            out_cols.append(c)
            continue

        def label_of(v: Any, labels: dict[str, str] = labels) -> str | None:
            return labels.get(str(v).strip())

        if mode == "both" and split:
            out_cols.append({**c, "label": f"{c.get('label') or key} {id_suffix}"})
            out_cols.append({**c, "key": key + LABEL_SUFFIX})
            for r in out_rows:
                v = r.get(key)
                r[key + LABEL_SUFFIX] = None if v is None else (label_of(v) or str(v).strip())
        else:
            out_cols.append(c)
            for r in out_rows:
                v = r.get(key)
                if v is not None:
                    r[key] = shown_text(str(v).strip(), label_of(v), mode)
    return out_cols, out_rows
