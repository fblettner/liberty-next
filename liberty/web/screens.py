"""``/api/screens`` — the per-app screen catalog, permission-pruned.

| Route | Permission | Notes |
|---|---|---|
| ``GET /api/screens`` | authenticated | every accessible screen, grouped by app — labels in the request's language, screens the caller can't read filtered out |
| ``GET /api/screens/{app}`` | authenticated | one app's screens (404 if no screens for the app, 404 if everything is filtered) |
| ``GET /api/screens/{app}/{screen_id}`` | per-screen ``sql:{connector}:{read_query}`` | one screen's full definition (dialog + actions + row_menu) |

A screen is kept iff the caller holds ``sql:{connector}:{read_query}`` — the same gate the
``TableView`` already checks before issuing the SELECT. The screen's effective ``connector``
(when not spelled out it's the app name) carries through to that permission check, so a
screen on a cross-pool query (e.g. an NOMAJDE menu pointing at a ``jdedwards`` query) gates
on *that* connector's permission.

Each row in the list response is the screen's ``public_dict()`` — `id`/`label`/`connector`/
the four CRUD query names + the flags. The single-screen route adds the dialog/actions/row_menu
so the frontend can render the form. The same ``X-Liberty-Lang`` header used by the rest of the
API drives label resolution.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from liberty.auth.dependencies import CurrentPrincipal
from liberty.auth.principal import Principal
from liberty.connectors import ConnectorRegistry
from liberty.connectors.config import ColumnHint
from liberty.connectors.dictionary import DictionaryFile
from liberty.connectors.sql import _hint_to_dict, _resolve_conditional_rule
from liberty.screens import Screen, ScreensFile
from liberty.web.deps import get_connectors, get_screens, request_language

router = APIRouter(prefix="/api", tags=["screens"])

Screens = Annotated[ScreensFile, Depends(get_screens)]
Connectors = Annotated[ConnectorRegistry, Depends(get_connectors)]


def _resolve_prompt_field(
    raw: dict[str, Any], *, connector: str, language: str | None, dictionary: DictionaryFile,
) -> dict[str, Any]:
    """Resolve one ``PromptField``'s ``dd`` against the shared dictionary in *language* and
    return a wire-ready dict the frontend can hand straight to its FieldRow renderer.

    Adds three keys alongside the raw fields (kept as-is):
      * ``label`` — the dictionary entry's translated label, when not already explicit.
      * ``format`` — the entry's format (``text`` / ``date`` / ``password`` / …) when not
        already explicit.
      * ``rule`` — the BOOLEAN / ENUM / LOOKUP display rule, or ``None`` if not display-relevant.

    Same convention :class:`liberty.connectors.sql.SQLConnector` uses to enrich result columns —
    so a prompt field renders with the exact same widget set (Checkbox / SearchSelect / Input)."""
    out = dict(raw)
    key = (out.get("dd") or out.get("name") or "").strip()
    if not key:
        return out  # nothing to look up — frontend falls back to plain-text widget
    entry = dictionary.find_entry(key, connector=connector)
    if entry is None:
        return out
    if not out.get("label"):
        lbl = entry.label_for(language)
        if lbl:
            out["label"] = lbl
    if not out.get("format") and entry.format:
        out["format"] = entry.format
    rule = dictionary.resolve_rule(entry, connector=connector, language=language)
    if rule is not None:
        out["rule"] = rule
    return out


def _resolve_screen_field(
    raw: dict[str, Any],
    *,
    column_hints: dict[str, "ColumnHint"],
    connector: str,
    language: str | None,
    dictionary: DictionaryFile,
) -> dict[str, Any]:
    """Build the wire-ready dialog-field payload entirely from the matching ``Screen.columns``
    entry — the SINGLE source of truth. A ``ScreenField`` only carries ``name`` (which column to
    render) + ``colspan`` (layout); every behaviour — label / format / rule / default / binds,
    hidden / disabled / required, the per-mode edit locks, and the conditional ``visible_when`` /
    ``required_when`` / ``disabled_when`` — comes from the column, resolved through the dictionary.
    The frontend's FieldRow reads everything from this one payload, never walking the columns.

    (Pre-unification a field could override the column per-dialog; that whole inherit-or-override
    layer is gone — the field is a pure reference, so there is nothing to merge.)

    Returns a fresh dict — never mutates the input."""
    from liberty.connectors.dictionary import DictionaryEntry
    name = (raw.get("name") or "").strip()
    if not name:
        return dict(raw)
    # Start from the field name; colspan + everything else come from the column (single source of
    # truth). A legacy field-level ``colspan`` still on the raw field is honoured as a fallback.
    out: dict[str, Any] = {"name": raw.get("name")}
    # Find the matching ColumnHint (case-insensitive on column name). Without one (a name that
    # isn't in Screen.columns) the field still resolves its widget via the dictionary by ``name``.
    hint = column_hints.get(name.lower())
    if (hint is not None and hint.colspan is not None):
        out["colspan"] = hint.colspan
    elif raw.get("colspan") is not None:
        out["colspan"] = raw.get("colspan")
    if hint is not None:
        if hint.dd is not None:
            out["dd"] = hint.dd
        if hint.label:
            out["label"] = hint.label
        if hint.format:
            out["format"] = hint.format
        if hint.default:
            out["default"] = hint.default
        if hint.lookup_param_binds:
            out["lookup_param_binds"] = [
                b.model_dump(mode="json", exclude_none=True) for b in hint.lookup_param_binds
            ]
        if hint.return_binds:
            out["return_binds"] = [
                {"param": b.param, "column": b.column} for b in hint.return_binds
            ]
        if hint.default_when:
            out["default_when"] = [w.as_dict() for w in hint.default_when]
        if hint.rules_when:
            out["rules_when"] = [
                {"field": w.field, "value": w.value,
                 "rule": _resolve_conditional_rule(w.rules, w.rules_values, dictionary, connector=connector, language=language, false_value=w.false_value),
                 # Per-rule binds (this rule's lookup gets these, not the column's base binds).
                 "lookup_param_binds": [b.model_dump(mode="json", exclude_none=True) for b in w.lookup_param_binds],
                 "return_binds": [{"param": b.param, "column": b.column} for b in w.return_binds]}
                for w in hint.rules_when
            ]
        # Static flags — straight from the column (always a concrete bool on the wire).
        out["hidden"] = bool(hint.hidden)
        out["disabled"] = bool(hint.disabled)
        out["required"] = bool(hint.required)
        if hint.disable_on_add:
            out["disable_on_add"] = True
        if hint.disable_on_edit:
            out["disable_on_edit"] = True
        # Conditional rules — all evaluated by the dialog against the live form. ``visible_when`` /
        # ``required_when`` / ``disabled_when`` share the {field, value} shape (VisibleWhen.as_dict).
        if hint.visible_when_rules:
            out["visible_when"] = [vw.as_dict() for vw in hint.visible_when_rules]
        if hint.required_when:
            out["required_when"] = [vw.as_dict() for vw in hint.required_when]
        if hint.disabled_when:
            out["disabled_when"] = [vw.as_dict() for vw in hint.disabled_when]
    else:
        out["hidden"] = False
        out["disabled"] = False
        out["required"] = False
    key = (out.get("dd") or name).strip()
    entry = dictionary.find_entry(key, connector=connector) if key else None
    # Effective format — hint-level wins; ``label`` already came from the hint or stays
    # absent (the frontend falls back to the read column's label).
    if not out.get("format"):
        fmt = entry.format if entry else None
        if fmt:
            out["format"] = fmt
    if entry is not None and not out.get("label"):
        lbl = entry.label_for(language)
        if lbl:
            out["label"] = lbl
    # Decide which rule applies. The column's ``rules`` / ``rules_values`` override → build a
    # transient entry from them. Otherwise use the dictionary entry's rule as-is (the SQL
    # connector's read-column path already resolves that; the frontend falls back to
    # ``column.rule`` if we don't ship a field-level one here).
    hint_rules = hint.rules.strip().upper() if (hint and hint.rules) else None
    hint_rules_values = hint.rules_values.strip() if (hint and hint.rules_values) else None
    if hint_rules:
        synth = DictionaryEntry(
            label=(entry.label if entry else None),
            format=out.get("format") or (entry.format if entry else None),
            rules=hint_rules,
            rules_values=hint_rules_values,
            # Carry the entry's ``lookup_params`` (the static IN-direction binds — UDC SY/RT etc.)
            # so a screen-level LOOKUP override still narrows the lookup correctly. The field's
            # own ``lookup_param_binds`` add the dynamic source-driven binds on top at runtime.
            lookup_params=(entry.lookup_params if entry else {}),
            false_value=(entry.false_value if entry else None),
            default=(out.get("default") or (entry.default if entry else None)),
        )
        rule = dictionary.resolve_rule(synth, connector=connector, language=language)
        if rule is not None:
            out["rule"] = rule
    elif entry is not None:
        rule = dictionary.resolve_rule(entry, connector=connector, language=language)
        if rule is not None:
            out["rule"] = rule
    return out


def _resolve_screen_fields_in_dialog(
    dialog: dict[str, Any] | None,
    *,
    column_hints: dict[str, ColumnHint],
    connector: str,
    language: str | None,
    dictionary: DictionaryFile,
) -> dict[str, Any] | None:
    """Walk a dumped ``ScreenDialog`` and resolve each form tab's fields from the screen's
    ``column_hints`` — every dialog field is a pure reference to one of this screen's columns
    (the single source of truth), so every tab uses the same *column_hints* map.

    The dialog's hooks + per-tab actions are resolved by :func:`_resolve_dialog_prompts`
    separately."""
    if not dialog:
        return dialog
    out = dict(dialog)
    tabs = out.get("tabs")
    if not isinstance(tabs, list):
        return out
    new_tabs = []
    for tab in tabs:
        if not isinstance(tab, dict):
            new_tabs.append(tab); continue
        new_tab = dict(tab)
        if isinstance(tab.get("fields"), list):
            new_tab["fields"] = [
                _resolve_screen_field(f, column_hints=column_hints, connector=connector, language=language, dictionary=dictionary)
                if isinstance(f, dict) else f
                for f in tab["fields"]
            ]
        new_tabs.append(new_tab)
    out["tabs"] = new_tabs
    return out


def _resolve_prompts_in_actions(
    actions: list[dict[str, Any]] | None,
    *,
    connector: str,
    language: str | None,
    dictionary: DictionaryFile,
) -> list[dict[str, Any]] | None:
    """In-place rewrite each action's ``prompt_fields`` to its resolved form (see
    :func:`_resolve_prompt_field`). Actions without prompts pass through untouched; non-list
    inputs (or ``None``) are returned as-is so this composes cleanly over ``model_dump``."""
    if not actions:
        return actions
    out: list[dict[str, Any]] = []
    for a in actions:
        if not isinstance(a, dict):
            out.append(a)
            continue
        fields = a.get("prompt_fields")
        if not fields:
            out.append(a)
            continue
        a = dict(a)
        a["prompt_fields"] = [
            _resolve_prompt_field(f, connector=connector, language=language, dictionary=dictionary)
            for f in fields
            if isinstance(f, dict)
        ]
        out.append(a)
    return out


def _resolve_dialog_prompts(
    dialog: dict[str, Any] | None,
    *,
    connector: str,
    language: str | None,
    dictionary: DictionaryFile,
) -> dict[str, Any] | None:
    """Walk a dumped ``ScreenDialog`` and resolve every action's prompt fields — the three lifecycle
    hooks (``on_load`` / ``on_save`` / ``on_cancel``) and each tab's per-tab ``actions``."""
    if not dialog:
        return dialog
    out = dict(dialog)
    for hook in ("on_load", "on_save", "on_cancel"):
        if hook in out:
            out[hook] = _resolve_prompts_in_actions(
                out.get(hook), connector=connector, language=language, dictionary=dictionary,
            )
    tabs = out.get("tabs")
    if isinstance(tabs, list):
        new_tabs = []
        for tab in tabs:
            if isinstance(tab, dict) and "actions" in tab:
                tab = {**tab, "actions": _resolve_prompts_in_actions(
                    tab.get("actions"), connector=connector, language=language, dictionary=dictionary,
                )}
            new_tabs.append(tab)
        out["tabs"] = new_tabs
    return out


def _screen_connector(screen: Screen, *, app: str) -> str:
    """Effective connector — explicit ``connector`` field if set, else the app name (matches
    the convention :mod:`liberty.migrations.v1.migrate_screens` uses to keep cross-pool
    references explicit and same-pool references implicit)."""
    return screen.connector or app


def _can_read(principal: Principal, screen: Screen, *, app: str) -> bool:
    perm = f"sql:{_screen_connector(screen, app=app)}:{screen.read_query}"
    return principal.has_permission(perm)


def _label_for(screen: Screen, *, language: str | None) -> str:
    """Display label for the list view — currently just ``label`` then ``description`` then
    the screen ``id``. (Per-language overrides on the screen itself land later — for now the
    *dictionary* drives translation when fields are rendered.)"""
    return screen.label or screen.description or screen.id


def _build_dd_map(screen: Screen) -> dict[str, str]:
    """``dd → column_name`` for the dashboard filter binding (one entry per ColumnHint with a
    non-empty ``dd``). First-seen wins on duplicates so reordering columns in the editor can
    never silently swap which column a dashboard's filter points at."""
    out: dict[str, str] = {}
    for h in screen.columns:
        if h.dd and h.dd not in out:
            out[h.dd] = h.name
    return out


def _list_view(screen: Screen, *, app: str, language: str | None) -> dict[str, Any]:
    """The compact descriptor returned by ``GET /api/screens`` — no dialog / actions / row_menu
    body, just enough for the frontend to (a) render the screen list and (b) decide whether to
    fetch the full screen body for a given (connector, query). The three ``has_*`` flags are the
    gate: when *any* is true the TableView fires ``GET /api/screens/{app}/{id}`` to pull the
    full body in (so right-click row menus appear even on screens without a dialog, and toolbar
    actions appear even without row menus, etc.)."""
    return {
        "id": screen.id,
        "app": app,
        "label": _label_for(screen, language=language),
        "description": screen.description,
        "connector": _screen_connector(screen, app=app),
        "read_query": screen.read_query,
        "update_query": screen.update_query,
        "insert_query": screen.insert_query,
        "delete_query": screen.delete_query,
        "auto_load": screen.auto_load,
        # Phase 3 — ``audit_table`` (string) replaces the legacy ``audit`` bool. The wire still
        # carries ``audit`` (bool) for back-compat with frontend code that flagged "is this
        # screen audited"; it's derived from ``audit_table`` presence.
        "audit": bool(screen.audit_table),
        "audit_table": screen.audit_table,
        "max_rows": screen.max_rows,
        # ``effective_key_columns`` folds the per-column ``key`` flags (Visual Designer Columns
        # tab) into the explicit ``key_columns`` list — operators set it either way and the
        # frontend reads the same result. The Pydantic field stays for explicit overrides.
        "key_columns": screen.effective_key_columns(),
        "editable": screen.editable,
        "uploadable": screen.uploadable,
        "initial_group_by": list(screen.initial_group_by),
        # Tree view config (parent/child/label column names) — None when this
        # screen is flat-table only. Frontend surfaces a third view toggle
        # ("Tree") on the TableView when this block is present.
        "treeview": screen.treeview.model_dump(mode="json") if screen.treeview else None,
        # Saved chart id (charts.toml) — when set, the TableView's Chart tab
        # pre-fills its spec from that chart instead of the empty session
        # default. Blank → keep the existing localStorage-seeded behaviour.
        "chart_id": screen.chart_id,
        "has_dialog": screen.dialog is not None,
        "has_row_menu": bool(screen.row_menu),
        "has_actions": bool(screen.actions),
        # Phase 1 — non-empty Screen.columns means the screen carries its own resolved column
        # hints (overrides the query's). The TableView fetches the full screen body when any of
        # the ``has_*`` flags is true; flagging this lets it pull the body even on screens with
        # no dialog / row_menu / actions, purely to pick up the screen-level column hints.
        "has_columns": bool(screen.columns),
        # ``dd_map`` — { dictionary_key: column_name } shipped on the list view so the dashboard
        # filter binding can resolve a filter's ``dictionary_key`` to *this* screen's matching
        # column name without fetching the full body. (Phase 3 follow-up: ColumnHint's ``dd``
        # used to ride on the connector's describe()'s per-query meta; dropping that left the
        # dashboard filters as a no-op until this map landed.) Empty when the screen lists no
        # columns or none of them have a ``dd`` — those screens just can't be filtered, same as
        # before. A column with the same ``dd`` listed twice is a config bug; we keep the first
        # so reordering Columns in the editor doesn't silently swap which one a dashboard binds.
        "dd_map": _build_dd_map(screen),
        # Row-click → sibling-screen dialog (the v1 "Display Properties" promotion). Carried on
        # the list view too so the frontend's TableView can wire row clicks without fetching the
        # full screen body (the property is only set on screens that need it; absent for most).
        "row_click_screen": screen.row_click_screen,
        "row_click_connector": screen.row_click_connector,
        "row_click_binds": [b.model_dump(mode="json", exclude_none=True) for b in screen.row_click_binds],
        # SPA-route drill-through (the escape hatch for screens whose detail view is a hand-written
        # React page, not a sibling Screen dialog — see Screen.row_click_route in screens/config.py).
        # Surfaced on the list view alongside row_click_screen so the TableView can decide which
        # path to take without fetching the full body.
        "row_click_route": screen.row_click_route,
    }


def _full_view(
    screen: Screen, *, app: str, language: str | None, dictionary: DictionaryFile,
) -> dict[str, Any]:
    """The full screen descriptor — ``GET /api/screens/{app}/{id}``. Includes the dialog/actions/row_menu
    body so the frontend can render the form. ``model_dump(mode='json')`` drops Pydantic's defaults that
    weren't set and gives us plain dicts ready to ship over JSON.

    Each action with ``prompt_fields`` gets its prompt fields enriched with their dictionary-resolved
    label / format / rule (BOOLEAN / ENUM / LOOKUP) so the frontend renders the prompt sub-dialog
    with the same widget set used for read-result columns. The screen's effective connector
    drives the dictionary lookup (matches how :class:`SQLConnector` resolves read-result hints)."""
    body = screen.model_dump(mode="json", exclude_none=True)
    connector = _screen_connector(screen, app=app)
    # **Dictionary scope** for resolving column dd/label/format/rule. The operator's metadata
    # (entries / enums / lookups) lives under the app's scope in dictionary.toml (one
    # ``[connectors.<app>]`` per migrated app). For a cross-pool screen (its ``connector`` is
    # set to a different data-pool — e.g. NOMAJDE's f00950 runs against ``jdedwards`` but its
    # dictionary entries live under ``connectors.nomajde``), the dictionary lookup follows the
    # **app**, not the data-pool. The LOOKUP rule's resolved ``connector`` field still carries
    # the lookup query's actual data pool (from the lookup_def).
    dict_scope = app
    if screen.columns:
        # Resolve each ColumnHint through the dictionary (label / format / rule), then *fold
        # in* any names listed in ``screen.key_columns`` as per-column ``key = true`` flags.
        # Pre-migration screens.toml files set keys via the flat ``screen.key_columns`` list;
        # the Visual Designer's Columns tab now reads ``column.key`` per column. Folding here
        # means existing configs light up the "key" badge / lock in the dialog without
        # needing a re-migration — and ``Screen.effective_key_columns()`` still returns the
        # same final list either way.
        key_set: set[str] = {k.upper() for k in screen.key_columns}
        body["columns"] = [
            {**_hint_to_dict(h, dictionary, language, connector=dict_scope),
             **({"key": True} if (h.key or h.name.upper() in key_set) else {})}
            for h in screen.columns
        ]
    else:
        body.pop("columns", None)
    # Resolve prompts on every attachment point that can carry actions: screen-level actions /
    # row_menu / on_insert / on_update / on_delete, and the dialog's hooks + per-tab buttons.
    for hook in ("actions", "row_menu", "on_insert", "on_update", "on_delete", "on_duplicate"):
        if hook in body:
            body[hook] = _resolve_prompts_in_actions(
                body.get(hook), connector=dict_scope, language=language, dictionary=dictionary,
            )
    if "dialog" in body:
        # Phase 2 — build a case-insensitive lookup of the screen's column hints once; the
        # dialog field resolver uses it to merge per-column display metadata onto each field
        # (so the v1 ``col_rules`` override actually drives the widget choice). A screen with
        # no ``columns`` list yields an empty map and the resolver falls back to dictionary
        # defaults — back-compat.
        ch: dict[str, ColumnHint] = {h.name.lower(): h for h in screen.columns}
        # Two passes — orthogonal concerns: ``_resolve_screen_fields_in_dialog`` enriches every
        # form tab's ``fields`` with the resolved ``rule`` / ``format`` / etc. from the matching
        # ColumnHint; ``_resolve_dialog_prompts`` resolves action ``prompt_fields`` on the
        # dialog's hooks + per-tab buttons.
        body["dialog"] = _resolve_screen_fields_in_dialog(
            body.get("dialog"), column_hints=ch,
            connector=dict_scope, language=language, dictionary=dictionary,
        )
        body["dialog"] = _resolve_dialog_prompts(
            body.get("dialog"), connector=dict_scope, language=language, dictionary=dictionary,
        )
    # Merge order matters: ``body`` is the raw model dump (the raw ``screen.key_columns``
    # list is empty when keys are declared via per-column ``key = true`` instead of the flat
    # field, same goes for the raw ``screen.connector`` vs the effective one). ``_list_view``
    # *computes* those values correctly — so spread it LAST so its values win over the raw
    # model fields. The dialog / actions / row_menu / columns body comes through unchanged
    # since ``_list_view`` doesn't define those keys.
    return {**body, **_list_view(screen, app=app, language=language)}


@router.get(
    "/screens",
    summary="List screens",
    responses={
        401: {"description": "Missing / invalid access token."},
    },
)
async def list_screens(request: Request, principal: CurrentPrincipal, screens: Screens) -> dict[str, Any]:
    """Per-app catalog of screens the caller can read. The response is
    ``{ "screens": { "<app>": [<list-view>, ...], ... } }`` — each entry is the compact
    list-view: id / label / connector / the four CRUD query names / flags
    (``has_dialog`` / ``has_row_menu`` / ``has_actions`` / ``has_columns``) and
    ``dd_map`` for dashboard filter wiring.

    Apps with no readable screens are dropped from the response. Labels honour the
    request's ``Accept-Language`` / ``X-Liberty-Lang`` header."""
    lang = request_language(request)
    out: dict[str, list[dict[str, Any]]] = {}
    for app, app_screens in screens.screens.items():
        kept = [
            _list_view(s, app=app, language=lang)
            for s in app_screens.values()
            if _can_read(principal, s, app=app)
        ]
        if kept:
            out[app] = kept
    return {"screens": out}


@router.get(
    "/screens/{app}",
    summary="List app screens",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "Unknown app, or every screen in the app is permission-filtered out."},
    },
)
async def list_app_screens(
    app: str, request: Request, principal: CurrentPrincipal, screens: Screens
) -> dict[str, Any]:
    """Narrow ``GET /api/screens`` to a single app. Returns 404 when the app exists but
    every screen is gated out (rather than an empty list) so the SPA's app switcher
    treats the app as unavailable rather than empty."""
    app_screens = screens.screens.get(app)
    if not app_screens:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No screens for app {app!r}")
    lang = request_language(request)
    kept = [
        _list_view(s, app=app, language=lang)
        for s in app_screens.values()
        if _can_read(principal, s, app=app)
    ]
    if not kept:
        # The app has screens, but the caller can't read any — keep parity with /api/menus' 404.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No accessible screens for app {app!r}")
    return {"app": app, "screens": kept}


@router.get(
    "/screens/{app}/{screen_id}",
    summary="Get screen",
    responses={
        401: {"description": "Missing / invalid access token."},
        404: {"description": "Unknown screen, OR the caller lacks ``sql:<connector>:<read_query>`` (collapsed to 404 to avoid leaking screen existence)."},
    },
)
async def get_screen(
    app: str,
    screen_id: str,
    request: Request,
    principal: CurrentPrincipal,
    screens: Screens,
    connectors: Connectors,
) -> dict[str, Any]:
    """Everything the React TableView needs to render the screen:

    - **Top-level fields** — the same list-view payload as ``GET /api/screens`` (id /
      label / connector / CRUD query names / flags / dd_map / row_click_*).
    - **columns** — each ColumnHint resolved against the dictionary in the request's
      language (label / format / lookup / rule). Drives the table headers, widgets, and
      formatting.
    - **dialog** — ScreenDialog with each tab's fields enriched from matching ColumnHints
      AND each action's ``prompt_fields`` resolved through the dictionary.
    - **actions / row_menu / on_insert / on_update / on_delete** — each action chain's
      prompt fields resolved (same machinery as dialog actions).
    - **chart_id / treeview / row_click_*** — additional view modes & drill-through wiring.

    Returns 404 (not 403) when the caller lacks ``sql:<connector>:<read_query>`` — same
    convention the connector routes follow so probing for screen names yields no
    information about what exists vs. what's forbidden."""
    app_screens = screens.screens.get(app) or {}
    screen = app_screens.get(screen_id)
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown screen {app!r}/{screen_id!r}")
    if not _can_read(principal, screen, app=app):
        # 404 (not 403) so we don't leak the existence of screens the caller can't open —
        # same convention the connector routes use.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown screen {app!r}/{screen_id!r}")
    return _full_view(
        screen,
        app=app,
        language=request_language(request),
        dictionary=connectors.dictionary,
    )
