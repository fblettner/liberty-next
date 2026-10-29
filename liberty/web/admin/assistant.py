"""``/admin`` — Screen Creation Assistant — one-shot scaffold (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _dump_screen, _snapshot_all_config, router
from liberty.web.admin.builders import (
    ConnectorsParsedBody,
    DictionaryBody,
    MenusBody,
    ScreensBody,
    _dictionary_path,
    put_connectors_parsed,
    put_dictionary_parsed,
    put_menus_parsed,
    put_screens_parsed,
)
from liberty.web.admin.bundles import reload_connectors
from pydantic import BaseModel, ValidationError

from liberty.config import (
    substitute_env,
)
from liberty.connectors.config import (
    ConnectorsFile,
    load_connectors_file,
)
from liberty.connectors.dictionary import (
    DictionaryFile,
    load_dictionary,
)
from liberty.menus import load_menus
from liberty.menus.config import MenusFile
from liberty.screens import load_screens
from liberty.screens.config import parse_screens


# ── Screen Creation Assistant — one-shot scaffold ─────────────────────────────────────────
# The assistant ("train") collects everything in the UI, builds the SELECT/CRUD SQL client-side
# (reusing the Query Wizard's generator), and POSTs ONE payload here. We merge it into the four
# config files and apply atomically: every file is validated FIRST (nothing written on any failure),
# then written through the SAME validated paths the per-section PUT endpoints use, then a single
# reload makes the new screen live. Reuse over reinvention — this is orchestration, not new I/O.
class ScaffoldTable(BaseModel):
    name: str                                   # base table name → queries <name>_get/_put/_post/_delete
    label: str | None = None
    get_sql: str                                # the generated SELECT (read_query)
    put_sql: str | None = None
    post_sql: str | None = None
    delete_sql: str | None = None


class ScaffoldMenuItem(BaseModel):
    app: str                                    # menu app (often == screen app)
    id: str
    label: str
    icon: str | None = None
    parent: str | None = None                   # id of an existing item to nest under (None = top level)
    target: str                                 # the screen id
    connector: str


class ScaffoldBody(BaseModel):
    connector: str                              # existing connector that receives the table/queries
    app: str                                    # app that receives the screen
    table: ScaffoldTable | None = None          # None → reuse an EXISTING query (screen.read_query already
                                                # points at it); no new connector table/queries are written
    dictionary_scope: str | None = None         # None / "shared" → top-level entries; else connectors.<scope>.entries
    dictionary: list[dict[str, Any]] = []       # each entry: {id, label?, format?, justify?, size?, rules?, ...}
    screen: dict[str, Any]                      # a parsed Screen dict (id, label, queries, columns[], dialog{tabs[]})
    menu: ScaffoldMenuItem | None = None


@router.post("/assistant/scaffold", summary="Scaffold a screen end-to-end")
async def assistant_scaffold(body: ScaffoldBody, request: Request, principal: Superuser) -> dict[str, Any]:
    """Create a connector table (+ its CRUD queries), optional dictionary entries, a screen + dialog,
    and an optional menu item — in one validated, reload-applied pass. Used by the Screen Creation
    Assistant. 404 if the target connector is unknown; 409 on a name collision; 422 if any merged
    file fails validation (in which case NOTHING is written)."""
    settings = request.app.state.settings

    # ── connectors.toml: append the new table to the target connector ───────────────────────
    cfile = load_connectors_file(Path(settings.connectors.config_path))
    if body.connector not in cfile.connectors:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown connector {body.connector!r}")
    conns = {name: c.model_dump(exclude_defaults=True) for name, c in cfile.connectors.items()}
    target = conns[body.connector]
    # When ``table`` is given, append the new table (+ its CRUD queries). When it's None the screen
    # reuses an EXISTING query (its ``read_query`` already names it) — connectors.toml is untouched.
    slots: list[str] = []
    if body.table is not None:
        tables = target.setdefault("tables", [])
        if any(isinstance(t, dict) and str(t.get("name", "")).lower() == body.table.name.lower() for t in tables):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"table {body.table.name!r} already exists on connector {body.connector!r}")
        table_entry: dict[str, Any] = {"name": body.table.name}
        if body.table.label:
            table_entry["label"] = body.table.label
        table_entry["get"] = {"sql": body.table.get_sql}
        slots.append("get")
        for slot, sql in (("put", body.table.put_sql), ("post", body.table.post_sql), ("delete", body.table.delete_sql)):
            if sql:
                table_entry[slot] = {"sql": sql, "writable": True}
                slots.append(slot)
        tables.append(table_entry)

    # ── dictionary.toml: merge entries (shared or per-connector scope) ───────────────────────
    dfile = load_dictionary(_dictionary_path(settings))
    ddict = dfile.model_dump(exclude_defaults=True)
    created_dd: list[str] = []
    if body.dictionary:
        if body.dictionary_scope and body.dictionary_scope != "shared":
            section = ddict.setdefault("connectors", {}).setdefault(body.dictionary_scope, {})
            entries = section.setdefault("entries", {})
        else:
            entries = ddict.setdefault("entries", {})
        for e in body.dictionary:
            eid = str(e.get("id") or "").strip()
            if not eid:
                continue
            entries[eid] = {k: v for k, v in e.items() if k != "id"}
            created_dd.append(eid)

    # ── screens.toml: add the screen under its app ──────────────────────────────────────────
    sfile = load_screens(Path(settings.screens.config_path))
    screens = {app: {sid: _dump_screen(s) for sid, s in scr.items()} for app, scr in sfile.screens.items()}
    sid = str(body.screen.get("id") or "").strip()
    if not sid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="screen.id is required")
    app_screens = screens.setdefault(body.app, {})
    if sid in app_screens:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"screen {body.app}.{sid} already exists")
    app_screens[sid] = body.screen

    # ── menus.toml: append the menu item ────────────────────────────────────────────────────
    mfile = load_menus(Path(settings.menus.config_path))
    menus = {app: am.model_dump(exclude_defaults=True) for app, am in mfile.menus.items()}
    if body.menu is not None:
        app_menu = menus.setdefault(body.menu.app, {})
        items = app_menu.setdefault("items", [])
        if any(isinstance(it, dict) and it.get("id") == body.menu.id for it in items):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"menu item {body.menu.app}.{body.menu.id} already exists")
        item: dict[str, Any] = {"id": body.menu.id, "label": body.menu.label, "type": "screen",
                                "target": body.menu.target, "connector": body.menu.connector}
        if body.menu.icon:
            item["icon"] = body.menu.icon
        if body.menu.parent:
            item["parent"] = body.menu.parent
        items.append(item)

    # ── validate EVERYTHING before writing anything (no partial scaffolds) ───────────────────
    try:
        ConnectorsFile.model_validate(substitute_env({"connectors": conns}))
        DictionaryFile.model_validate(ddict)
        parse_screens({"screens": screens})
        MenusFile.model_validate({"menus": menus})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"scaffold validation failed: {exc}") from exc

    # ── write through the existing validated PUT paths (each snapshots its file); reload once ─
    _snapshot_all_config(request)
    if body.table is not None:
        await put_connectors_parsed(ConnectorsParsedBody(connectors=conns), request, principal)
    if body.dictionary:
        await put_dictionary_parsed(DictionaryBody(dictionary=ddict), request, principal)
    await put_screens_parsed(ScreensBody(screens=screens), request, principal)
    if body.menu is not None:
        await put_menus_parsed(MenusBody(menus=menus), request, principal)
    reload_result = await reload_connectors(request, principal)

    return {
        "created": {
            "connector": body.connector,
            "table": body.table.name if body.table is not None else None,
            "queries": [f"{body.table.name}_{s}" for s in slots] if body.table is not None else [],
            "dictionary_entries": created_dd,
            "screen": f"{body.app}.{sid}",
            "menu_item": body.menu.id if body.menu is not None else None,
        },
        "reload": reload_result,
    }


@router.get("/assistant/presets", summary="List screen-assistant table presets")
async def list_assistant_presets(request: Request, _: Superuser, connector: str | None = None) -> dict[str, Any]:
    """Operator-managed table presets for the Screen Assistant — a catalog of ready-made base
    table + joins so an operator can start a screen without browsing the schema (e.g. the JDE
    Address Book). Read from TOML under ``<config_dir>/presets/`` (recursively); each file holds a
    ``[[presets]]`` list of ``{id, label, description?, connector?, tables: [{schema?, name, alias?,
    join?, on?: [{left, right}], key?: [item…]}]}`` — the first table is the base, the rest carry a
    join + ON; ``key`` is the table's primary key as data items (the explorer's entity key).
    Filtered by ``connector`` when given (a preset with no ``connector`` matches any)."""
    from liberty.presets import load_presets, presets_dir
    return {"presets": load_presets(presets_dir(request.app.state.settings.connectors.config_path), connector)}
