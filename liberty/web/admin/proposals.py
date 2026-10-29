"""``/admin`` — apply an AI-assistant proposal (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, _snapshot_config, router
from liberty.web.admin.builders import _dictionary_path
from liberty.web.admin.bundles import reload_connectors
from pydantic import BaseModel, ValidationError

from liberty.connectors.config import (
    SqlConnectorConfig,
    load_connectors_file,
)
from liberty.connectors.dictionary import (
    DictionaryFile,
    load_dictionary,
    parse_dictionary,
)
from liberty.menus import load_menus
from liberty.menus.config import MenusFile, parse_menus
from liberty.screens import load_screens
from liberty.screens.config import ScreensFile, parse_screens

# ── Apply an AI-assistant proposal ──────────────────────────────────────────────────────────
# The AI assistant's scaffold tools never write to disk — they return a Proposal envelope
# (see liberty.ai.proposal) which the chat UI surfaces as an Apply card. Clicking Apply POSTs
# the envelope here; this endpoint merges the payload into the right config file via the same
# validation + write paths the dedicated PUT endpoints use, then runs /admin/reload so the new
# config is live. Returns ``{applied: true}`` plus a short summary so the chat can confirm.
#
# Dispatching by ``kind`` keeps the chat UI from needing to know 4+ endpoint paths — one knob.


class ProposalApplyBody(BaseModel):
    """A Proposal envelope as emitted by ``liberty.ai.proposal.Proposal.to_dict()``."""

    kind: str  # dictionary_entries | connector_queries | screen | menu_item
    summary: str | None = None
    target_path: str | None = None
    payload: dict[str, Any]
    app: str | None = None
    connector: str | None = None
    notes: list[str] | None = None


def _apply_dictionary_entries(request: Request, payload: dict[str, Any]) -> str:
    """Merge dictionary entries into the shared scope or a connector overlay."""
    scope = payload.get("scope")
    entries = payload.get("entries") or {}
    if not scope or not isinstance(entries, dict) or not entries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {scope, entries}")
    # Normalise common synonyms — the assistant occasionally uses ``type`` instead of the
    # DictionaryEntry's actual ``format`` field. Quietly rename so a sloppy proposal still
    # validates. Same kind of forgiveness the parsed-config PUTs already offer (extra=ignore
    # on ScreenField, etc.) but here Pydantic's extra='forbid' would reject the key outright.
    normalised: dict[str, dict[str, Any]] = {}
    for eid, rec in entries.items():
        if not isinstance(rec, dict):
            continue
        rec = dict(rec)
        if "type" in rec and "format" not in rec:
            rec["format"] = rec.pop("type")
        normalised[eid] = rec
    entries = normalised
    path = _dictionary_path(request.app.state.settings)
    cfg = load_dictionary(path)
    doc = cfg.model_dump(exclude_defaults=True)
    if scope == "shared":
        merged = {**(doc.get("entries") or {}), **entries}
        doc["entries"] = merged
    else:
        connectors = doc.get("connectors") or {}
        section = connectors.get(scope) or {}
        section_entries = {**(section.get("entries") or {}), **entries}
        section["entries"] = section_entries
        connectors[scope] = section
        doc["connectors"] = connectors
    # Re-validate + write — same pattern as put_dictionary_parsed
    try:
        validated = DictionaryFile.model_validate(doc)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged dictionary invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps(validated.model_dump(exclude_defaults=True))
    parse_dictionary(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return f"merged {len(entries)} entries into {scope!r} scope"


def _apply_connector_queries(request: Request, payload: dict[str, Any]) -> str:
    """Append (or replace by name) queries on a SQL connector. Uses tomlkit so other
    connectors / comments in connectors.toml round-trip untouched."""
    connector = payload.get("connector")
    queries = payload.get("queries") or []
    if not connector or not isinstance(queries, list) or not queries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {connector, queries}")
    path = Path(request.app.state.settings.connectors.config_path)
    cfg = load_connectors_file(path)
    conn = cfg.connectors.get(connector)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connector {connector!r} not found")
    if conn.type != "sql":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"connector {connector!r} is not a SQL connector")
    existing = conn.model_dump(exclude_defaults=True)
    # Bucket the incoming proposals into the four sections with the SAME classifier the loader
    # uses (``type`` field wins, else CRUD-suffix guess → table). It drops the stray ``type``
    # field, so an earlier scaffold revision's bad per-slot kind (select / insert / …) falls
    # through to the name-pattern fallback cleanly. Then merge per section:
    #   * queries / sequences / lookups — replace-by-name, append new.
    #   * tables — merge by table name: incoming slots + non-null label/description win, new
    #     tables are appended. (A clone of a CRUD screen re-forms its ``<base>_<suffix>_<crud>``
    #     slots back into one table here.)
    from liberty.connectors.config import _split_legacy_queries
    incoming = _split_legacy_queries(connector, [dict(q) for q in queries if q.get("name")])
    for section in ("queries", "sequences", "lookups"):
        by_name = {q.get("name"): q for q in (existing.get(section) or [])}
        for q in incoming.get(section) or []:
            by_name[q.get("name")] = q
        merged = list(by_name.values())
        if merged:
            existing[section] = merged
    tbl_by_name: dict[Any, dict[str, Any]] = {t.get("name"): t for t in (existing.get("tables") or [])}
    for t in incoming.get("tables") or []:
        cur = tbl_by_name.get(t.get("name"))
        if cur is None:
            tbl_by_name[t.get("name")] = t
            continue
        for meta_key in ("label", "description"):
            if t.get(meta_key) is not None:
                cur[meta_key] = t[meta_key]
        for crud in ("get", "put", "post", "delete"):
            if crud in t:
                cur[crud] = t[crud]
    if tbl_by_name:
        existing["tables"] = list(tbl_by_name.values())
    try:
        SqlConnectorConfig.model_validate(existing)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged connector invalid: {exc}") from exc

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    connectors_tbl = doc.get("connectors")
    if connectors_tbl is None:
        connectors_tbl = tomlkit.table(is_super_table=True)
        doc["connectors"] = connectors_tbl
    # Replace the connector's subtree wholesale — preserves [pools.*] + other connectors + comments
    # outside this connector. The connector's own subtree gets re-rendered (any inline columns become
    # nested tables — same trade-off as put_connectors_parsed).
    connectors_tbl[connector] = existing
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return f"added/updated {len(queries)} queries on connector {connector!r}"


def _apply_screen(request: Request, payload: dict[str, Any]) -> str:
    """Add or replace a screen under [screens.<app>.<screen_id>]."""
    app = payload.get("app")
    screen_id = payload.get("screen_id")
    screen = payload.get("screen") or {}
    if not app or not screen_id or not isinstance(screen, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {app, screen_id, screen}")
    path = Path(request.app.state.settings.screens.config_path)
    registry = load_screens(path)
    # ``registry`` stores Screen instances directly per (app, id); rebuild the wire-shape doc
    doc = {a: {sid: s.model_dump(exclude_defaults=False, exclude_none=True) for sid, s in screens.items()}
           for a, screens in registry.screens.items()}
    doc.setdefault(app, {})[screen_id] = screen
    try:
        ScreensFile.model_validate({"screens": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged screens invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"screens": doc})
    parse_screens(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return f"saved screen {app}.{screen_id}"


def _apply_menu_item(request: Request, payload: dict[str, Any]) -> str:
    """Append a menu item to [menus.<app>]. If an item with the same id already exists, replace it."""
    app = payload.get("app")
    item = payload.get("item") or {}
    if not app or not isinstance(item, dict) or not item.get("id"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="proposal payload must carry {app, item: {id, ...}}")
    path = Path(request.app.state.settings.menus.config_path)
    cfg = load_menus(path)
    doc = {name: app_menu.model_dump(exclude_defaults=False, exclude_none=True) for name, app_menu in cfg.menus.items()}
    app_doc = doc.get(app) or {"items": []}
    items = list(app_doc.get("items") or [])
    items = [it for it in items if it.get("id") != item["id"]]
    items.append(item)
    app_doc["items"] = items
    doc[app] = app_doc
    try:
        MenusFile.model_validate({"menus": doc})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"merged menus invalid: {exc}") from exc
    import tomli_w
    new_text = tomli_w.dumps({"menus": doc})
    parse_menus(tomllib.loads(new_text))
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return f"saved menu item {item['id']} under {app}"


_APPLY_DISPATCH: dict[str, Any] = {
    "dictionary_entries": _apply_dictionary_entries,
    "connector_queries": _apply_connector_queries,
    "screen": _apply_screen,
    "menu_item": _apply_menu_item,
}


@router.post("/config/apply-proposal", summary="Apply AI proposal")
async def apply_proposal(body: ProposalApplyBody, request: Request, _: Superuser) -> dict[str, object]:
    """Apply a proposal emitted by the AI assistant's scaffold tools. Dispatches to the
    matching merge/write path based on ``kind``, then runs /admin/reload so the new config is
    live. Returns ``{applied, kind, summary}``.

    All write paths are the same Pydantic-validated + tomli_w / tomlkit serialisers the
    dedicated PUT endpoints use — no fast paths, no skipped validation. A bad proposal lands
    as a 422 with the validation error, same as a hand-edited PUT."""
    fn = _APPLY_DISPATCH.get(body.kind)
    if fn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown proposal kind: {body.kind!r}")
    summary = fn(request, body.payload)
    # Reload the live registries so the new config is in effect for the next chat turn (the
    # "applied; continue" follow-up the assistant gets needs to see the writes it just made).
    await reload_connectors(request, _)  # type: ignore[arg-type]
    return {"applied": True, "kind": body.kind, "summary": summary}
