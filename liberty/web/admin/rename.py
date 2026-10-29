"""``/admin`` — rename top-level keys (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_all_config, router
from liberty.web.admin.builders import _dictionary_path
from pydantic import BaseModel

from liberty.web.move import MoveError, move_query
from liberty.web.rename import (
    RenameError,
    rename_action,
    rename_connector,
    rename_dictionary_entry,
    rename_lookup,
    rename_query,
    rename_screen,
    rename_screen_app,
    rename_sequence,
    rename_table,
)

# ── rename top-level keys (Phase-7 loose ends) ─────────────────────────────────────────────


class RenameBody(BaseModel):
    """Payload for ``POST /admin/config/rename``. ``kind`` selects the rename flavour:

    * ``"connector"`` — top-level ``[connectors.<old>]`` in :file:`connectors.toml` + every
      cross-file ``connector = "<old>"`` reference (screens / menus / dictionary / dashboards
      / charts).
    * ``"sequence"`` / ``"lookup"`` — ``[sequences.<old>]`` / ``[lookups.<old>]`` in
      :file:`dictionary.toml` + the matching ``DictionaryEntry.rules_values`` references.
      ``scope`` optional: connector name to target ``[connectors.<scope>.sequences.<old>]`` etc.
    * ``"screen_app"`` — ``[screens.<old>]`` in :file:`screens.toml` + the matching
      ``[menus.<old>]`` in :file:`menus.toml` (if it exists). ``scope`` ignored.
    * ``"dictionary_entry"`` — ``[entries.<old>]`` in :file:`dictionary.toml` + every
      ``ColumnHint.dd`` / ``PromptField.dd`` in :file:`screens.toml` + ``SequenceDef.dd_id``
      and ``LookupDef.return_params`` references in the same scope. ``scope`` optional.
    * ``"query"`` — a query name in ``[[connectors.<scope>.queries]]`` + every reference scoped
      to that connector (screens' read/update/insert/delete_query + run_query/navigate/export,
      dictionary sequence/lookup ``query``, chart/widget ``query``, sql menu leaves' ``target``).
      ``scope`` is **required** (the connector).
    * ``"table"`` — a CRUD base: renames every existing ``<old>_<get|put|post|delete>`` slot +
      the screen bindings, in one pass. ``scope`` is **required** (the connector).
    """

    kind: str
    old_name: str
    new_name: str
    scope: str | None = None


@router.post("/config/rename", summary="Rename entity (cross-file)")
async def rename_top_level_key(body: RenameBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Rename a top-level config key + every cross-file reference in one atomic pass.

    The structured builders edit each file's body wholesale via tomlkit, but a top-level key
    is referenced from several files. Renaming by hand means hunting every reference —
    error-prone and easy to miss a deeply-nested step in an action chain. This endpoint walks
    every affected file, rewrites the references via :mod:`liberty.web.rename`, validates each
    rewritten doc against its Pydantic schema, then writes them all in one batch. On any
    validation failure nothing is written.

    Does **not** reload — the caller calls ``POST /admin/reload`` after to apply changes
    everywhere (in-flight requests still see the old registry until they finish).
    """
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    settings = request.app.state.settings
    try:
        if body.kind == "connector":
            result = rename_connector(
                body.old_name, body.new_name,
                connectors_path=Path(settings.connectors.config_path),
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
                dictionary_path=_dictionary_path(settings),
                dashboards_path=Path(settings.dashboards.config_path),
                charts_path=Path(settings.charts.config_path),
            )
        elif body.kind == "sequence":
            result = rename_sequence(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                screens_path=Path(settings.screens.config_path),
                scope=body.scope,
            )
        elif body.kind == "lookup":
            result = rename_lookup(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                screens_path=Path(settings.screens.config_path),
                scope=body.scope,
            )
        elif body.kind == "screen_app":
            result = rename_screen_app(
                body.old_name, body.new_name,
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
            )
        elif body.kind == "dictionary_entry":
            result = rename_dictionary_entry(
                body.old_name, body.new_name,
                dictionary_path=_dictionary_path(settings),
                screens_path=Path(settings.screens.config_path),
                scope=body.scope,
            )
        elif body.kind in ("query", "table"):
            if not body.scope:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"a {body.kind} rename requires `scope` (the connector name)")
            fn = rename_query if body.kind == "query" else rename_table
            result = fn(
                body.old_name, body.new_name,
                connector=body.scope,
                connectors_path=Path(settings.connectors.config_path),
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
                dictionary_path=_dictionary_path(settings),
                charts_path=Path(settings.charts.config_path),
                dashboards_path=Path(settings.dashboards.config_path),
            )
        elif body.kind == "screen":
            if not body.scope:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="a screen rename requires `scope` (the app name)")
            result = rename_screen(
                body.old_name, body.new_name,
                app=body.scope,
                screens_path=Path(settings.screens.config_path),
                menus_path=Path(settings.menus.config_path),
            )
        elif body.kind == "action":
            result = rename_action(
                body.old_name, body.new_name,
                actions_path=Path(settings.actions.config_path),
                screens_path=Path(settings.screens.config_path),
            )
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"rename kind {body.kind!r} not supported — one of: connector, sequence, lookup, screen, screen_app, dictionary_entry, query, table, action",
            )
    except RenameError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()


class MoveBody(BaseModel):
    """Payload for ``POST /admin/config/move`` — move a query definition between connectors.

    ``kind`` is one of ``table`` / ``query`` / ``sequence`` / ``lookup`` (the connectors.toml
    section that holds the definition). ``name`` is the entry's name (a table base, e.g.
    ``f0004`` — its ``<base>_<crud>`` slots move together). ``from_connector`` / ``to_connector``
    are the source and (already-existing) target connectors."""

    kind: str
    name: str
    from_connector: str
    to_connector: str


@router.post("/config/move", summary="Move query between connectors (cross-file)")
async def move_query_between_connectors(body: MoveBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Relocate a table / query / sequence / lookup definition from one connector to another and
    rewrite every safe reference (dictionary lookups/sequences, menus, charts, dashboard widgets,
    actions, and screens whose connector isn't shared with queries left behind). References that
    can't be auto-rewritten come back in ``manual_refs`` for the operator to fix.

    Atomic — nothing is written unless every rewritten doc re-parses. Does **not** reload; the
    caller runs ``POST /admin/reload`` after to apply the change."""
    _snapshot_all_config(request)  # cross-file mutation — snapshot all config first
    settings = request.app.state.settings
    try:
        result = move_query(
            body.kind, body.name, body.from_connector, body.to_connector,
            connectors_path=Path(settings.connectors.config_path),
            screens_path=Path(settings.screens.config_path),
            menus_path=Path(settings.menus.config_path),
            dictionary_path=_dictionary_path(settings),
            charts_path=Path(settings.charts.config_path),
            dashboards_path=Path(settings.dashboards.config_path),
            actions_path=Path(settings.actions.config_path),
        )
    except MoveError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return result.to_dict()
