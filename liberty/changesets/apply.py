"""Apply a change-package bundle to a target environment — replay the net ops with full pre-image
drift detection.

For each op the target's CURRENT row is read (via the op's ``read_query`` narrowed by the natural
key) and compared, column by column, to the captured ``old_values``:

* INSERT — a row with that key must NOT already exist (else conflict).
* UPDATE / DELETE — the row must exist AND match the pre-image; a value someone changed in the
  target since capture is a conflict (don't silently overwrite it).

A conflict blocks the op unless it's force-listed. ``dry_run`` does the checks + reports without
writing. Comparison is trim-tolerant (JDE CHAR padding). When the op carries no ``read_query`` /
key, drift can't be verified — reported as ``unverified`` (applied only if forced or dry-run).
"""

from __future__ import annotations

from typing import Any

from liberty.connectors.config import ColumnHint


def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _get_ci(row: dict[str, Any], col: str) -> Any:
    if col in row:
        return row[col]
    lc = col.lower()
    for k, v in row.items():
        if k.lower() == lc:
            return v
    return None


def _op_summary(op: dict[str, Any]) -> dict[str, Any]:
    return {
        "connector": op.get("connector"), "query": op.get("query"), "operation": op.get("operation"),
        "entity": op.get("entity"), "entity_key": op.get("entity_key"),
        "post_apply": bool(op.get("post_apply")), "label": op.get("label"),
    }


async def _read_current_row(connectors: Any, op: dict[str, Any]) -> dict[str, Any] | None:
    """Read the target's current row for *op* via its ``read_query`` narrowed by the natural key as
    equality filters (reusing the runtime filter wrap, so JDE CHAR padding is handled). Returns the
    row dict, or None if no such row. Raises if the read query can't run."""
    read_query = op.get("read_query")
    key = op.get("entity_key") or {}
    conn = connectors.sql(op["connector"])
    hints = [ColumnHint(name=k, filter=True) for k in key]
    params: dict[str, Any] = {}
    for k, v in key.items():
        params[k] = v
        params[f"{k}_op"] = "equals"
    res = await conn.execute(read_query, params, column_hints=hints)
    rows = res.rows or []
    return rows[0] if rows else None


async def check_drift(connectors: Any, op: dict[str, Any]) -> tuple[str, str]:
    """Return ``(verdict, detail)`` — verdict ∈ ``ok`` | ``conflict`` | ``unverified``."""
    if op.get("post_apply"):
        # A run-once post-apply step (e.g. a remerge) appended to the bundle tail — always runs.
        return ("unverified", "post-apply step — runs once after the change ops")
    if (op.get("operation") or "").upper() in ("CALL_API", "CALL_PLUGIN"):
        # An opted-in screen action (API/plugin). It's re-run verbatim on apply; its effects are
        # opaque so there's nothing to pre-image. Always unverified — the operator sees it'll fire.
        return ("unverified", "action replay — re-runs on apply; side effects can't be drift-checked")
    if not op.get("read_query") or not (op.get("entity_key")):
        return ("unverified", "no read query / natural key captured — drift can't be checked")
    try:
        current = await _read_current_row(connectors, op)
    except Exception as exc:  # noqa: BLE001 — surface as unverified, don't crash the whole apply
        return ("unverified", f"could not read the target row: {type(exc).__name__}: {exc}")

    if op["operation"] == "INSERT":
        return ("conflict", "a row with this key already exists in the target") if current is not None else ("ok", "")
    # UPDATE / DELETE
    if current is None:
        return ("conflict", "the row no longer exists in the target")
    diffs = [
        f"{col}: target={_norm(_get_ci(current, col))!r} ≠ captured={_norm(want)!r}"
        for col, want in (op.get("old_values") or {}).items()
        if _norm(_get_ci(current, col)) != _norm(want)
    ]
    if diffs:
        return ("conflict", "target differs from the captured pre-image — " + "; ".join(diffs))
    return ("ok", "")


async def _execute_op(connectors: Any, op: dict[str, Any], *, settings: Any = None) -> int:
    """Run the op. For a row op (INSERT/UPDATE/DELETE) it runs the write query, binding a superset —
    the new values + the key + the key's ``_ORIGINAL`` form (the migrated UPDATE/DELETE WHERE keys
    on ``:<col>_ORIGINAL``); the query uses whichever binds it references. For an invocation op
    (CALL_API / CALL_PLUGIN) it RE-RUNS the captured call against this environment's connector —
    the same paths the screen action used (``APIConnector.call`` / ``invoke_callable``)."""
    op_type = (op.get("operation") or "").upper()
    new = op.get("new_values") or {}
    if op_type == "CALL_API":
        conn = connectors.api(op["connector"])
        result = await conn.call(op["query"], dict(new))   # op["query"] is the endpoint name
        if not getattr(result, "success", True):
            raise RuntimeError(
                f"API replay failed: {getattr(result, 'error', None) or getattr(result, 'status_code', '?')}"
            )
        return 1
    if op_type == "CALL_PLUGIN":
        from liberty.jobs.steps.base import RunContext
        from liberty.jobs.triggers import ManualTrigger
        from liberty.plugins.invoke import invoke_callable
        ctx = RunContext(
            run_id="changeset-apply", job_id="__changeset__",
            trigger=ManualTrigger(triggered_by="changeset-apply"),
        )
        await invoke_callable(
            op["query"], dict(new),       # op["query"] is the ``module:function`` callable
            injections=(("connectors", connectors), ("ctx", ctx), ("settings", settings)),
        )
        return 1
    # ── Row op (SQL) ──
    conn = connectors.sql(op["connector"])
    key = op.get("entity_key") or {}
    params = {**new, **key, **{f"{k}_ORIGINAL": v for k, v in key.items()}}
    res = await conn.execute(op["query"], params)
    return res.rowcount if res.rowcount is not None else -1


async def apply_bundle(
    connectors: Any, bundle: dict[str, Any], *, dry_run: bool, forced: set[str], settings: Any = None,
) -> dict[str, Any]:
    """Replay *bundle*'s ops in order with drift detection. ``forced`` holds the op indices (as
    strings) the operator chose to apply despite a conflict. ``settings`` is injected into any
    CALL_PLUGIN replay. Returns a per-op report + summary."""
    ops = bundle.get("ops") or []
    results: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        verdict, detail = await check_drift(connectors, op)
        is_forced = str(i) in forced
        if verdict == "conflict" and not is_forced:
            results.append({"index": i, "op": _op_summary(op), "status": "conflict", "detail": detail})
            continue
        if dry_run:
            status = "would_apply" if verdict == "ok" else ("would_force" if is_forced else verdict)
            results.append({"index": i, "op": _op_summary(op), "status": status, "detail": detail})
            continue
        try:
            rc = await _execute_op(connectors, op, settings=settings)
            note = " (forced over conflict)" if (verdict == "conflict" and is_forced) else ""
            results.append({"index": i, "op": _op_summary(op), "status": "applied", "rowcount": rc, "detail": (detail + note).strip()})
        except Exception as exc:  # noqa: BLE001 — record + continue; the operator sees the per-op error
            results.append({"index": i, "op": _op_summary(op), "status": "error", "detail": f"{type(exc).__name__}: {exc}"})

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"dry_run": dry_run, "op_count": len(ops), "summary": counts, "results": results}
