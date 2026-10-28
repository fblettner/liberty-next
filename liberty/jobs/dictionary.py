"""Built-in NomaFlow callable: the dictionary entries an explorer model's tables lack, created
from the source's data dictionary — the Explorer page's "create the dictionary entries", as a
job. A scan is one query per table (on JDE the ``ALL_TAB_COLUMNS`` ↔ F92xx join), so a model of
fifteen tables can outlast a request through a proxy: run it as a job, with its log, from a
``job`` menu item instead.

    [[jobs]]
    id = "nomajde-dictionary-refresh"
    [[jobs.steps]]
    type = "python"
    name = "refresh"
    callable = "liberty.jobs.dictionary:refresh_model_dictionary"
    [jobs.steps.op_kwargs]
    app = "nomajde"          # the explorer app; its models unless ``models`` names some

The python-step executor injects ``connectors`` / ``settings`` / ``ctx`` / ``app_state`` by name;
with ``app_state`` the connectors are rebuilt afterwards so the labels reach every query
(without it — a bare test wiring — the next reload picks them up).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

_log = logging.getLogger(__name__)


async def refresh_model_dictionary(
    app: str,
    *,
    connectors: Any,
    settings: Any,
    models: list[str] | str | None = None,
    scope: str | None = None,
    ctx: Any = None,
    app_state: Any = None,
) -> int:
    """Create the missing entries for the explorer models of *app* (every model, or the ids in
    *models* — a list or a comma-separated string), under the dictionary scope *scope* (default:
    the app). Returns the number of entries created (the step's ``rows_affected``); every table
    scanned, skipped or created is logged."""
    from liberty.explorer.config import load_explorer
    from liberty.presets import presets_dir
    from liberty.web.dictgen import create_entries, model_sources

    catalogue = presets_dir(settings.connectors.config_path)
    explorer = load_explorer(settings.explorer.config_path, presets_dir=catalogue)
    raw = models.split(",") if isinstance(models, str) else list(models or [])
    wanted = [str(m).strip() for m in raw if str(m).strip()]   # "" / blanks = every model
    mine = [(mid, m) for a, mid, m in explorer.items() if a == app]
    unknown = sorted(set(wanted) - {mid for mid, _ in mine})
    if unknown:
        raise ValueError(f"unknown explorer model(s) for {app!r}: {unknown} (known: {[mid for mid, _ in mine]})")
    chosen = [(mid, m) for mid, m in mine if not wanted or mid in wanted]
    if not chosen:
        raise ValueError(f"no explorer model for app {app!r}")
    run = getattr(ctx, "run_id", None)
    created_total = 0
    for mid, m in chosen:
        sources = model_sources(m, catalogue)
        out = await create_entries(
            registry=connectors, settings=settings, scope=scope or app, sources=sources,
            versions=getattr(app_state, "config_versions", None),
        )
        created_total += len(out["created"])
        _log.info(
            "nomaflow.dictionary run=%s model=%s/%s tables=%d created=%d%s",
            run, app, mid, out["scanned"], len(out["created"]), (" skipped: " + "; ".join(out["skipped"])) if out["skipped"] else "",
        )
        if out["created"]:
            _log.info("nomaflow.dictionary run=%s model=%s/%s entries: %s", run, app, mid, ", ".join(out["created"]))
    if created_total and app_state is not None:
        from liberty.runtime import rebuild_runtime
        # rebuild_runtime works on ``app.state``; the executor hands the state itself
        await rebuild_runtime(SimpleNamespace(state=app_state), sections={"connectors"})
        _log.info("nomaflow.dictionary run=%s connectors rebuilt: %d entries live", run, created_total)
    return created_total
