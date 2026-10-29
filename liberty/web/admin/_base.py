"""``/admin`` — the router, the shared helpers (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.screens import Screen

router = APIRouter(prefix="/admin", tags=["admin"])

Superuser = Annotated[Principal, Depends(require_superuser)]

_log = logging.getLogger("liberty.admin")


def _snapshot_config(request: Request, path: Path) -> None:
    """Snapshot a config file's CURRENT bytes before a save overwrites it — the input to config
    history / diff / restore. Best-effort: a versioning failure must never block the save."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        return
    try:
        store.snapshot(path, source="manual")
    except Exception:  # noqa: BLE001 — never fail a save over versioning
        _log.warning("config snapshot failed for %s", path, exc_info=True)


def _snapshot_all_config(request: Request) -> None:
    """Snapshot EVERY config file before a cross-file mutation (rename / move / clone / delete /
    import) — these write outside the per-section ``PUT /parsed`` path and can touch several files at
    once, so we capture the whole pre-op state. Deduped (unchanged files skip), best-effort."""
    store = getattr(request.app.state, "config_versions", None)
    if store is None:
        return
    s = request.app.state.settings
    paths = [
        s.connectors.config_path,
        s.connectors.dictionary_path or s.connectors.config_path.with_name("dictionary.toml"),
        s.menus.config_path, s.screens.config_path, s.charts.config_path,
        s.actions.config_path, s.dashboards.config_path,
    ]
    for p in paths:
        if not p:
            continue
        try:
            store.snapshot(Path(p), source="manual")
        except Exception:  # noqa: BLE001 — never fail an op over versioning
            _log.warning("config snapshot failed for %s", p, exc_info=True)


def _reinject_action_type(a_dict: dict[str, Any], a_model: Any) -> None:
    """Re-inject the discriminator on an Action dict + every nested sub-list (so the frontend
    union parser picks the right variant when round-tripping). ``model_dump(exclude_defaults=True)``
    strips ``type`` because every variant's ``type: Literal[X] = X`` matches its own default —
    without this fix, an :class:`IfAction` reaches the builder as ``{id, condition, then_steps,
    else_steps}`` with no ``type`` field, and the frontend defaults the type picker to ``run_query``.

    Slice 4d added :class:`ChainAction.steps` / :class:`IfAction.then_steps` / ``else_steps`` /
    :class:`LoopAction.steps` — every one of those is a nested ``list[Action]`` that needs the
    same treatment recursively."""
    a_dict["type"] = a_model.type
    # Recurse into the four step-list-bearing variants. The model attribute name matches the
    # dict key, so a single ``getattr + dict.get`` zip covers each case.
    for nested_key in ("steps", "then_steps", "else_steps"):
        nested_list = a_dict.get(nested_key)
        nested_models = getattr(a_model, nested_key, None)
        if not nested_list or not nested_models:
            continue
        for sub_dict, sub_model in zip(nested_list, nested_models, strict=False):
            _reinject_action_type(sub_dict, sub_model)


def _dump_screen(s: Screen) -> dict[str, Any]:
    """Default-stripped model dump + re-injected discriminators for every tab / action."""
    d = s.model_dump(exclude_defaults=True)
    # Tabs — discriminator on each ScreenTab variant.
    if s.dialog is not None:
        for tab_dict, tab_model in zip(d.get("dialog", {}).get("tabs", []), s.dialog.tabs, strict=False):
            tab_dict["type"] = tab_model.type
            # Per-tab actions — recursive so nested ChainAction.steps / IfAction.then_steps /
            # else_steps / LoopAction.steps all keep their discriminators.
            for a_dict, a_model in zip(tab_dict.get("actions", []), tab_model.actions, strict=False):
                _reinject_action_type(a_dict, a_model)
        # Dialog-level hook chains.
        for hook in ("on_load", "on_save", "on_cancel"):
            for a_dict, a_model in zip(d.get("dialog", {}).get(hook, []), getattr(s.dialog, hook, []), strict=False):
                _reinject_action_type(a_dict, a_model)
    # Screen-level action lists.
    for hook in ("actions", "row_menu", "on_insert", "on_update", "on_delete", "on_duplicate"):
        for a_dict, a_model in zip(d.get(hook, []), getattr(s, hook, []), strict=False):
            _reinject_action_type(a_dict, a_model)
    # Fold ``screen.key_columns`` (the old flat list — what pre-Phase-3 migration emitted
    # and what hand-edited screens may still use) into each matching column hint as
    # ``key: True``. The Visual Designer's Columns tab reads ``column.key`` per column;
    # without this fold, an existing ``key_columns = [...]`` line would silently disappear
    # from the UI on load. Saving back through PUT then writes ``key: true`` on the columns
    # and drops the legacy list — file converges to the new shape after one round-trip.
    if s.key_columns:
        # Case-insensitive match, preserve the operator's original case for any leftover.
        key_set = {k.upper() for k in s.key_columns}
        dumped_cols = d.get("columns")
        if isinstance(dumped_cols, list):
            matched: set[str] = set()
            for c in dumped_cols:
                if isinstance(c, dict):
                    nm = str(c.get("name", "")).upper()
                    if nm in key_set:
                        c["key"] = True
                        matched.add(nm)
            leftover = [k for k in s.key_columns if k.upper() not in matched]
            if leftover:
                d["key_columns"] = leftover     # unmatched keys keep the explicit list
            else:
                d.pop("key_columns", None)       # all keys landed on column hints — clean
    return d


def _drop_none(value: Any) -> Any:
    """*value* without ``None`` leaves: dict keys holding None are removed, lists keep their
    order (a None list element becomes an empty string — TOML arrays cannot hold a hole)."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) if v is not None else "" for v in value]
    return value
