"""FastAPI dependencies + helpers for the web layer.

The connector routes are permission-gated, but the required permission string
depends on path params (``sql:{connector}:{query}``), so the check is done
imperatively in the handler via :func:`require_permission` rather than as a
``Depends`` factory. Everything else (the registry, the principal) comes through
ordinary dependencies.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from liberty.auth.principal import Principal
from liberty.connectors import ConnectorRegistry
from liberty.menus import MenusFile
from liberty.screens import ScreensFile


def get_connectors(request: Request) -> ConnectorRegistry:
    return request.app.state.connectors


def get_menus(request: Request) -> MenusFile:
    return request.app.state.menus


def get_screens(request: Request) -> ScreensFile:
    return request.app.state.screens


def request_language(request: Request) -> str | None:
    """The UI/result language for a request: the ``X-Liberty-Lang`` header (the SPA sends its
    current i18n language), else the first ``Accept-Language`` tag (region stripped), else
    ``None`` → fall back to the relevant ``default_language``."""
    h = (request.headers.get("x-liberty-lang") or "").strip()
    if h:
        return h
    al = request.headers.get("accept-language")
    if al:
        first = al.split(",")[0].split(";")[0].strip()
        return first.split("-")[0] or None
    return None


def require_permission(principal: Principal, permission: str) -> None:
    """Raise 403 unless *principal* holds *permission* (glob-aware)."""
    if not principal.has_permission(permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")


def public_query(query: dict) -> dict:
    """A SQL-query descriptor with the SQL text stripped — safe for HTTP responses."""
    return {k: v for k, v in query.items() if k != "sql"}


def public_connector(desc: dict, principal: Principal) -> dict | None:
    """Filter one connector descriptor to what *principal* may use; ``None`` if nothing.

    SQL connectors carry the sectioned :meth:`SQLConnector.describe` shape (``tables`` /
    ``queries`` / ``sequences`` / ``lookups``). We emit BOTH:

      * a **flat** ``queries`` list — every permitted query across all four sections, with
        table CRUD slots flattened to their synthesised ``<table>_<crud>`` name. This is the
        shape the runtime TableView and the screen-designer pickers have always read, so
        they need no change.
      * the **sectioned** shape (``tables`` / ``queries`` / ``sequences`` / ``lookups``),
        permission-filtered, for consumers that want the table grouping.

    A query is permitted under ``sql:<connector>:<query-name>``. SQL text is stripped from
    every emitted descriptor via :func:`public_query`."""
    name = desc["name"]
    if desc["type"] == "sql":
        def _allowed(q: dict) -> bool:
            return principal.has_permission(f"sql:{name}:{q['name']}")

        tables: list[dict] = []
        flat: list[dict] = []
        for tbl in desc.get("tables", []):
            slots = [public_query(s) for s in tbl.get("slots", []) if _allowed(s)]
            flat.extend(slots)
            if slots:
                tables.append({
                    "name": tbl.get("name"),
                    "label": tbl.get("label"),
                    "description": tbl.get("description"),
                    "slots": slots,
                })
        sections: dict[str, list[dict]] = {}
        for key in ("queries", "sequences", "lookups"):
            kept = [public_query(q) for q in desc.get(key, []) if _allowed(q)]
            sections[key] = kept
            flat.extend(kept)
        if not flat:
            # Distinguish "no queries at all" from "queries exist but all forbidden for this user".
            # A truly-empty connector that still opts into the switcher (``show_in_switcher``) is an
            # APP whose screens read from OTHER connectors — e.g. nomajde after its tables/lookups
            # were moved to jdedwards. Keep it (with empty query lists) so the app switcher, which
            # filters /api/connectors by menu app name, can still find it. A permission-hidden
            # connector (it HAS queries, just none this caller may use) still returns None — no leak.
            raw_total = (
                sum(len(t.get("slots", [])) for t in desc.get("tables", []))
                + len(desc.get("queries", [])) + len(desc.get("sequences", [])) + len(desc.get("lookups", []))
            )
            if raw_total == 0 and desc.get("show_in_switcher", True):
                return {
                    "name": name, "type": "sql", "queries": [], "tables": [],
                    "customs": [], "sequences": [], "lookups": [],
                    "show_in_switcher": desc.get("show_in_switcher", True),
                }
            return None
        # Sort the flat list by name so every query-picker dropdown (screen designer, charts,
        # column groups, exports, actions…) lists queries alphabetically rather than in section
        # order (all the CRUD slots first, then customs/sequences/lookups). Case-insensitive.
        flat.sort(key=lambda q: str(q.get("name", "")).lower())
        return {
            "name": name, "type": "sql",
            "queries": flat,                       # backwards-compat flat list, name-sorted
            "tables": tables,
            "customs": sections["queries"],        # sectioned custom queries
            "sequences": sections["sequences"],
            "lookups": sections["lookups"],
            "show_in_switcher": desc.get("show_in_switcher", True),
            # Pool NAMES this connector may run against (multi-environment picker). Never the
            # pool URL/credentials — just identifiers. Absent/1-element ⇒ single-pool connector.
            "pools": desc.get("pools", []),
        }
    if desc["type"] == "api":
        endpoints = [
            e for e in desc["endpoints"]
            if principal.has_permission(f"api:{name}:{e['name']}")
        ]
        if not endpoints:
            return None
        return {"name": name, "type": "api", "base_url": desc.get("base_url"),
                "auth_type": desc.get("auth_type"), "endpoints": endpoints,
                "show_in_switcher": desc.get("show_in_switcher", True)}
    return None  # pragma: no cover - guarded by the discriminated union
