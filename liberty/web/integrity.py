"""Config integrity checks — the inverse of :mod:`liberty.web.usages`.

``usages`` answers "what points AT this entity?". Integrity answers "does everything an
entity points at actually exist?" — it walks every cross-reference in the loaded config
(menus / screens / dictionary / dashboards / charts / connectors) and reports broken
references (errors) plus dead-weight smells (unused connectors, orphan screens — warnings).

Powers ``GET /admin/config/integrity`` → the Settings → Integrity tab. Each issue carries a
``deep_link`` (same shape as a :class:`Usage`) so the UI can route straight to the offender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from liberty.web.usages import (
    _chart_deep_link,
    _connector_deep_link,
    _connector_query_deep_link,
    _dashboard_deep_link,
    _dict_deep_link,
    _find_query_usages,
    _get_dictionary,
    _iter_dict_sections,
    _iter_screen_actions,
    _iter_shared_action_steps,
    _menu_deep_link,
    _screen_deep_link,
)

_CRUD = ("get", "put", "post", "delete")


@dataclass(slots=True)
class Issue:
    """One integrity problem. ``severity`` is ``"error"`` (a broken reference — something
    won't work) or ``"warning"`` (a smell — unused / orphaned, still valid). ``category`` groups
    issues in the UI; ``deep_link`` routes to the offending entity's editor."""

    severity: str
    category: str
    message: str
    deep_link: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "category": self.category, "message": self.message, "deep_link": self.deep_link}


# ── reference indexes ────────────────────────────────────────────────────────────────────

def _sql_query_names(conn: Any) -> set[str] | None:
    """Every flat query name on a SQL connector (tables' CRUD slots + custom / sequences /
    lookups). ``None`` when the connector isn't SQL (so callers can tell "API connector" from
    "SQL connector with no such query")."""
    cfg = getattr(conn, "config", None)
    it = getattr(cfg, "iter_named_queries", None)
    if it is None:
        return None
    try:
        return {name for name, _ in it()}
    except Exception:                     # noqa: BLE001 — a malformed connector → treat as empty
        return set()


def _api_endpoint_names(conn: Any) -> set[str] | None:
    cfg = getattr(conn, "config", None)
    eps = getattr(cfg, "endpoints", None)
    if eps is None:
        return None
    return {str(getattr(e, "name", "")) for e in eps}


@dataclass
class _Index:
    conn_names: set[str]
    sql_by_conn: dict[str, set[str]]        # connector → flat SQL query names
    api_by_conn: dict[str, set[str]]        # connector → endpoint names
    screens_by_app: dict[str, set[str]]     # app → screen ids
    read_query_by_app: dict[str, dict[str, str]]  # app → {screen_id: read_query}
    menu_items_by_app: dict[str, set[str]]  # app → menu item ids
    dashboard_ids: set[str]
    charts_by_scope: dict[str, set[str]]
    homes: dict[str, str | None]            # connector → home menu-item id
    shared_action_ids: set[str]             # actions.toml [actions.<id>] — call_action targets


def _build_index(state: Any) -> _Index:
    connectors = getattr(state, "connectors", None)
    screens = getattr(state, "screens", None)
    menus = getattr(state, "menus", None)
    dashboards = getattr(state, "dashboards", None)
    charts = getattr(state, "charts", None)

    conn_names: set[str] = set()
    sql_by_conn: dict[str, set[str]] = {}
    api_by_conn: dict[str, set[str]] = {}
    homes: dict[str, str | None] = {}
    if connectors is not None:
        for name in connectors.names():
            conn_names.add(name)
            conn = connectors.get(name)
            sql = _sql_query_names(conn)
            if sql is not None:
                sql_by_conn[name] = sql
            api = _api_endpoint_names(conn)
            if api is not None:
                api_by_conn[name] = api
            homes[name] = getattr(getattr(conn, "config", None), "home", None)

    screens_by_app: dict[str, set[str]] = {}
    read_query_by_app: dict[str, dict[str, str]] = {}
    if screens is not None:
        for app, smap in (getattr(screens, "screens", None) or {}).items():
            screens_by_app[app] = set(smap.keys())
            read_query_by_app[app] = {sid: getattr(s, "read_query", "") for sid, s in smap.items()}

    menu_items_by_app: dict[str, set[str]] = {}
    if menus is not None:
        for app, am in (getattr(menus, "menus", None) or {}).items():
            menu_items_by_app[app] = {getattr(it, "id", "") for it in (getattr(am, "items", None) or [])}

    dashboard_ids: set[str] = set()
    if dashboards is not None:
        for scope, dmap in (getattr(dashboards, "dashboards", None) or {}).items():
            if isinstance(dmap, dict):
                # A dashboard's public id (what menus target) is the qualified ``<scope>.<id>``.
                dashboard_ids.update(f"{scope}.{did}" for did in dmap.keys())

    charts_by_scope: dict[str, set[str]] = {}
    if charts is not None:
        for scope, cmap in (getattr(charts, "charts", None) or {}).items():
            charts_by_scope[scope] = set(cmap.keys()) if isinstance(cmap, dict) else set()

    actions = getattr(state, "actions", None)
    shared_action_ids: set[str] = set((getattr(actions, "actions", None) or {}).keys()) if actions is not None else set()

    return _Index(
        conn_names=conn_names, sql_by_conn=sql_by_conn, api_by_conn=api_by_conn,
        screens_by_app=screens_by_app, read_query_by_app=read_query_by_app,
        menu_items_by_app=menu_items_by_app, dashboard_ids=dashboard_ids,
        charts_by_scope=charts_by_scope, homes=homes, shared_action_ids=shared_action_ids,
    )


# ── checks ───────────────────────────────────────────────────────────────────────────────

def _check_menus(state: Any, idx: _Index, out: list[Issue]) -> None:
    menus = getattr(state, "menus", None)
    if menus is None:
        return
    for app, am in (getattr(menus, "menus", None) or {}).items():
        for it in getattr(am, "items", None) or []:
            itype = getattr(it, "type", None)
            target = getattr(it, "target", None)
            conn = getattr(it, "connector", None) or app
            iid = getattr(it, "id", "?")
            label = getattr(it, "label", None) or iid
            link = _menu_deep_link(app, iid)
            if itype in (None, "page", "dashboard"):
                if itype == "dashboard" and target and target not in idx.dashboard_ids:
                    out.append(Issue("error", "Broken menu target",
                                     f"{app} · \"{label}\" → dashboard '{target}' does not exist", link))
                continue
            # query / screen / endpoint carry a connector
            if conn not in idx.conn_names:
                out.append(Issue("error", "Missing connector",
                                 f"{app} · \"{label}\" → connector '{conn}' does not exist", link))
                continue
            if itype == "screen":
                if target and target not in idx.screens_by_app.get(app, set()):
                    out.append(Issue("error", "Broken menu target",
                                     f"{app} · \"{label}\" → screen '{target}' does not exist", link))
            elif itype == "query":
                if target and target not in idx.sql_by_conn.get(conn, set()):
                    out.append(Issue("error", "Broken menu target",
                                     f"{app} · \"{label}\" → query '{conn}.{target}' does not exist", link))
            elif itype == "endpoint":
                if target and target not in idx.api_by_conn.get(conn, set()):
                    out.append(Issue("error", "Broken menu target",
                                     f"{app} · \"{label}\" → endpoint '{conn}.{target}' does not exist", link))


def _check_screens(state: Any, idx: _Index, out: list[Issue]) -> None:
    screens = getattr(state, "screens", None)
    if screens is None:
        return
    # Dictionary ids (lookup / enum / sequence) per scope — for validating a column's rule references
    # (a screen-level ``rules`` override or a conditional ``rules_when``) point at something real.
    dictionary = _get_dictionary(state)
    dict_ids: dict[str, dict[str, set[str]]] = {}
    if dictionary is not None:
        for sc, section in _iter_dict_sections(dictionary):
            dict_ids[sc] = {coll: set((section.get(coll) or {}).keys()) for coll in ("lookups", "enums", "sequences")}
    _RULE_COLL = {"LOOKUP": "lookups", "ENUM": "enums", "SEQUENCE": "sequences", "NN": "sequences"}

    def _check_rule_ref(app: str, sid: str, link: dict[str, Any], rules: Any, rv: Any, where: str) -> None:
        # A column's rule scope is the screen's app (dict_scope = app), with a shared fallback.
        coll = _RULE_COLL.get(rules.upper()) if isinstance(rules, str) else None
        if not coll or not rv:
            return
        if rv not in dict_ids.get(app, {}).get(coll, set()) and rv not in dict_ids.get("shared", {}).get(coll, set()):
            out.append(Issue("error", f"Broken {coll[:-1]} reference",
                             f"{app}.{sid} · {where} → {rules.upper()} '{rv}' is not defined in scope '{app}' or shared", link))

    for app, smap in (getattr(screens, "screens", None) or {}).items():
        app_screens = idx.screens_by_app.get(app, set())
        for sid, s in smap.items():
            conn = getattr(s, "connector", None) or app
            link = _screen_deep_link(app, sid)
            if conn not in idx.conn_names:
                out.append(Issue("error", "Missing connector",
                                 f"{app}.{sid} · connector '{conn}' does not exist", link))
            sql = idx.sql_by_conn.get(conn, set())
            for attr in ("read_query", "update_query", "insert_query", "delete_query"):
                q = getattr(s, attr, None)
                if q and conn in idx.conn_names and q not in sql:
                    out.append(Issue("error", "Missing query",
                                     f"{app}.{sid} · {attr} '{q}' does not exist on {conn}", link))
            rcs = getattr(s, "row_click_screen", None)
            if rcs and rcs not in app_screens:
                out.append(Issue("error", "Broken screen reference",
                                 f"{app}.{sid} · row_click_screen '{rcs}' does not exist", link))
            chart_id = getattr(s, "chart_id", None)
            if chart_id and chart_id not in idx.charts_by_scope.get(conn, set()):
                out.append(Issue("error", "Broken chart reference",
                                 f"{app}.{sid} · chart_id '{chart_id}' does not exist on {conn}", link))
            # column groups (related 1:1 write-back tables) — validate their write queries + that
            # every column's ``group`` references a real group.
            group_ids = set()
            for grp in getattr(s, "column_groups", None) or []:
                gid = getattr(grp, "id", None)
                if gid:
                    group_ids.add(gid)
                g_conn = getattr(grp, "connector", None) or conn
                g_sql = idx.sql_by_conn.get(g_conn, set())
                for attr in ("update_query", "insert_query", "delete_query"):
                    q = getattr(grp, attr, None)
                    if q and g_conn in idx.conn_names and q not in g_sql:
                        out.append(Issue("error", "Missing query",
                                         f"{app}.{sid} · column group '{gid}' {attr} '{q}' does not exist on {g_conn}", link))
            for col in getattr(s, "columns", None) or []:
                cg = getattr(col, "group", None)
                if cg and cg not in group_ids:
                    out.append(Issue("error", "Broken column group",
                                     f"{app}.{sid} · column '{getattr(col, 'name', '?')}' group '{cg}' is not defined in column_groups", link))
                cname = getattr(col, "name", "?")
                # Screen-level rule override + each conditional rules_when → a real lookup/enum/sequence.
                _check_rule_ref(app, sid, link, getattr(col, "rules", None), getattr(col, "rules_values", None), f"column '{cname}' rules")
                for rw in getattr(col, "rules_when", None) or []:
                    _check_rule_ref(app, sid, link, getattr(rw, "rules", None), getattr(rw, "rules_values", None), f"column '{cname}' rules_when")
            # dialog tabs — nested_table / nested_form both reference another screen by id.
            dialog = getattr(s, "dialog", None)
            for tab in (getattr(dialog, "tabs", None) or []) if dialog is not None else []:
                ttype = getattr(tab, "type", None)
                if ttype == "nested_table":
                    tscreen = getattr(tab, "screen", None)
                    if tscreen and tscreen not in app_screens:
                        out.append(Issue("error", "Broken screen reference",
                                         f"{app}.{sid} · tab[{getattr(tab, 'id', '?')}] nested_table screen '{tscreen}' does not exist", link))
                elif ttype == "nested_form":
                    # A nested_form reuses another screen's form — validate the reference exists.
                    fscreen = getattr(tab, "form_screen", None)
                    if fscreen and fscreen not in app_screens:
                        out.append(Issue("error", "Broken screen reference",
                                         f"{app}.{sid} · tab[{getattr(tab, 'id', '?')}] nested_form form_screen '{fscreen}' does not exist", link))
            # actions — navigate (to / screen), run_query (query), call_api (endpoint)
            for where, action in _iter_screen_actions(s):
                atype = getattr(action, "type", None)
                aconn = getattr(action, "connector", None) or conn
                if atype == "navigate":
                    nav_screen = getattr(action, "screen", None)
                    if nav_screen and nav_screen not in idx.screens_by_app.get(aconn, app_screens):
                        out.append(Issue("error", "Broken screen reference",
                                         f"{app}.{sid} · {where} navigate → screen '{nav_screen}' does not exist", link))
                    to = getattr(action, "to", None)
                    if to and aconn in idx.conn_names and to not in idx.sql_by_conn.get(aconn, set()):
                        out.append(Issue("error", "Missing query",
                                         f"{app}.{sid} · {where} navigate → query '{aconn}.{to}' does not exist", link))
                elif atype == "run_query":
                    q = getattr(action, "query", None)
                    if q and aconn in idx.conn_names and q not in idx.sql_by_conn.get(aconn, set()):
                        out.append(Issue("error", "Missing query",
                                         f"{app}.{sid} · {where} run_query '{aconn}.{q}' does not exist", link))
                elif atype == "call_api":
                    ep = getattr(action, "endpoint", None)
                    if ep and aconn in idx.conn_names and ep not in idx.api_by_conn.get(aconn, set()):
                        out.append(Issue("error", "Missing endpoint",
                                         f"{app}.{sid} · {where} call_api '{aconn}.{ep}' does not exist", link))
                elif atype == "call_action":
                    ref = getattr(action, "ref", None)
                    if ref and ref not in idx.shared_action_ids:
                        out.append(Issue("error", "Broken action reference",
                                         f"{app}.{sid} · {where} call_action → shared action '{ref}' is not defined", link))


def _check_shared_actions(state: Any, idx: _Index, out: list[Issue]) -> None:
    """Validate every SHARED action's steps (actions.toml) — the same run_query / call_api /
    call_action reference checks ``_check_screens`` runs on a screen's actions. A shared action is
    reused across screens, so a broken ref here silently breaks every caller; the per-screen scan
    never reaches it (it only walks screen hooks). A step pins its own connector — when it's unset
    we can't resolve the query/endpoint's owner, so that step is skipped rather than mis-flagged."""
    for aid, step in _iter_shared_action_steps(state):
        atype = getattr(step, "type", None)
        aconn = getattr(step, "connector", None)
        link = {"editor": "actions", "action": aid}
        if atype == "run_query":
            q = getattr(step, "query", None)
            if q and aconn in idx.conn_names and q not in idx.sql_by_conn.get(aconn, set()):
                out.append(Issue("error", "Missing query",
                                 f"action {aid} · run_query '{aconn}.{q}' does not exist", link))
        elif atype == "call_api":
            ep = getattr(step, "endpoint", None)
            if ep and aconn in idx.conn_names and ep not in idx.api_by_conn.get(aconn, set()):
                out.append(Issue("error", "Missing endpoint",
                                 f"action {aid} · call_api '{aconn}.{ep}' does not exist", link))
        elif atype == "call_action":
            ref = getattr(step, "ref", None)
            if ref and ref not in idx.shared_action_ids:
                out.append(Issue("error", "Broken action reference",
                                 f"action {aid} · call_action → shared action '{ref}' is not defined", link))


def _check_dictionary(state: Any, idx: _Index, out: list[Issue]) -> None:
    dictionary = _get_dictionary(state)
    if dictionary is None:
        return
    for scope, section in _iter_dict_sections(dictionary):
        for coll, kind in (("lookups", "lookup"), ("sequences", "sequence")):
            for name, entry in (section.get(coll) or {}).items():
                conn = getattr(entry, "connector", None) or (None if scope == "shared" else scope)
                q = getattr(entry, "query", None)
                if not q or conn is None:
                    continue
                if conn not in idx.conn_names:
                    out.append(Issue("error", "Missing connector",
                                     f"{scope} · {kind} '{name}' → connector '{conn}' does not exist", _dict_deep_link(coll, scope, name)))
                elif q not in idx.sql_by_conn.get(conn, set()):
                    out.append(Issue("error", "Missing query",
                                     f"{scope} · {kind} '{name}' → query '{conn}.{q}' does not exist", _dict_deep_link(coll, scope, name)))


def _check_dashboards_charts(state: Any, idx: _Index, out: list[Issue]) -> None:
    charts = getattr(state, "charts", None)
    if charts is not None:
        for scope, cmap in (getattr(charts, "charts", None) or {}).items():
            if not isinstance(cmap, dict):
                continue
            for cid, chart in cmap.items():
                conn = getattr(chart, "connector", None) or scope
                q = getattr(chart, "query", None)
                if q and conn in idx.conn_names and q not in idx.sql_by_conn.get(conn, set()):
                    out.append(Issue("error", "Missing query",
                                     f"chart {scope}.{cid} → query '{conn}.{q}' does not exist", _chart_deep_link(scope, cid)))
    dashboards = getattr(state, "dashboards", None)
    if dashboards is not None:
        for scope, dmap in (getattr(dashboards, "dashboards", None) or {}).items():
            if not isinstance(dmap, dict):
                continue
            for did, dash in dmap.items():
                dconn = getattr(dash, "connector", None)
                for w in getattr(dash, "widgets", None) or []:
                    wconn = getattr(w, "connector", None) or dconn or scope
                    wq = getattr(w, "query", None)
                    if wq and wconn in idx.conn_names and wq not in idx.sql_by_conn.get(wconn, set()):
                        out.append(Issue("error", "Missing query",
                                         f"dashboard {did} · widget → query '{wconn}.{wq}' does not exist", _dashboard_deep_link(did)))


def _check_homes(state: Any, idx: _Index, out: list[Issue]) -> None:
    for conn, home in idx.homes.items():
        if home and home not in idx.menu_items_by_app.get(conn, set()):
            out.append(Issue("error", "Broken home",
                             f"connector '{conn}' home → menu item '{home}' does not exist", _connector_deep_link(conn)))


def _check_unused_connectors(state: Any, idx: _Index, out: list[Issue]) -> None:
    """A connector referenced by nothing AND with no menu of its own is dead weight."""
    referenced: set[str] = set(idx.menu_items_by_app.keys())  # apps with a menu count as "used"
    # screens — the screen's own connector PLUS every connector its dialog tabs, actions
    # (run_query / call_api / navigate), and export sheets target (a connector used only by, say,
    # a call_api action is still in use).
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, smap in (getattr(screens, "screens", None) or {}).items():
            for _sid, s in smap.items():
                screen_conn = getattr(s, "connector", None) or app
                referenced.add(screen_conn)
                dialog = getattr(s, "dialog", None)
                for tab in (getattr(dialog, "tabs", None) or []) if dialog is not None else []:
                    if getattr(tab, "connector", None):
                        referenced.add(tab.connector)
                for _where, action in _iter_screen_actions(s):
                    if getattr(action, "connector", None):
                        referenced.add(action.connector)
                export = getattr(s, "export", None)
                for sheet in (getattr(export, "sheets", None) or []) if export is not None else []:
                    if getattr(sheet, "connector", None):
                        referenced.add(sheet.connector)
    # shared actions — a connector targeted only by a reusable action's run_query / call_api step
    for _aid, step in _iter_shared_action_steps(state):
        if getattr(step, "connector", None):
            referenced.add(step.connector)
    # menus
    menus = getattr(state, "menus", None)
    if menus is not None:
        for app, am in (getattr(menus, "menus", None) or {}).items():
            for it in getattr(am, "items", None) or []:
                referenced.add(getattr(it, "connector", None) or app)
    # dictionary lookup/sequence connectors + scopes
    dictionary = _get_dictionary(state)
    if dictionary is not None:
        for scope, section in _iter_dict_sections(dictionary):
            if scope != "shared":
                referenced.add(scope)
            for coll in ("lookups", "sequences"):
                for _n, e in (section.get(coll) or {}).items():
                    c = getattr(e, "connector", None)
                    if c:
                        referenced.add(c)
    # charts + dashboards
    charts = getattr(state, "charts", None)
    if charts is not None:
        for scope, cmap in (getattr(charts, "charts", None) or {}).items():
            referenced.add(scope)
            for _c in (cmap or {}).values() if isinstance(cmap, dict) else []:
                if getattr(_c, "connector", None):
                    referenced.add(_c.connector)
    dashboards = getattr(state, "dashboards", None)
    if dashboards is not None:
        for _scope, dmap in (getattr(dashboards, "dashboards", None) or {}).items():
            for d in (dmap or {}).values() if isinstance(dmap, dict) else []:
                if getattr(d, "connector", None):
                    referenced.add(d.connector)
                for w in getattr(d, "widgets", None) or []:
                    if getattr(w, "connector", None):
                        referenced.add(w.connector)
    for name in sorted(idx.conn_names - referenced):
        out.append(Issue("warning", "Unused connector",
                         f"connector '{name}' is referenced by nothing and has no menu", _connector_deep_link(name)))


def _check_orphan_screens(state: Any, idx: _Index, out: list[Issue]) -> None:
    """A screen reached from no menu and no drill (nested_table / row_click / navigate) — likely
    dead, or a drill target the operator forgot to wire."""
    reached: set[tuple[str, str]] = set()   # (app, screen_id)
    menus = getattr(state, "menus", None)
    if menus is not None:
        for app, am in (getattr(menus, "menus", None) or {}).items():
            for it in getattr(am, "items", None) or []:
                itype = getattr(it, "type", None)
                conn = getattr(it, "connector", None) or app
                target = getattr(it, "target", None)
                if itype == "screen" and target:
                    reached.add((app, target))
                elif itype == "query" and target:
                    # a query menu opens whichever screen has that read query on this connector
                    for sid, rq in idx.read_query_by_app.get(app, {}).items():
                        if rq == target:
                            reached.add((app, sid))
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, smap in (getattr(screens, "screens", None) or {}).items():
            for _sid, s in smap.items():
                conn = getattr(s, "connector", None) or app
                rcs = getattr(s, "row_click_screen", None)
                if rcs:
                    reached.add((app, rcs))
                dialog = getattr(s, "dialog", None)
                for tab in (getattr(dialog, "tabs", None) or []) if dialog is not None else []:
                    if getattr(tab, "type", None) == "nested_table" and getattr(tab, "screen", None):
                        reached.add((app, tab.screen))
                    # A nested_form reuses another screen's form → that screen is reached.
                    if getattr(tab, "type", None) == "nested_form" and getattr(tab, "form_screen", None):
                        reached.add((app, tab.form_screen))
                for _where, action in _iter_screen_actions(s):
                    if getattr(action, "type", None) != "navigate":
                        continue
                    aconn = getattr(action, "connector", None) or conn
                    nav_screen = getattr(action, "screen", None)
                    if nav_screen:
                        reached.add((aconn, nav_screen))
                    # A navigate to a QUERY opens the screen whose read query matches it (the
                    # runtime resolves /sql/<conn>/<to> → a screen by read query) — so that screen
                    # is reachable too, same as a query-type menu leaf.
                    nav_to = getattr(action, "to", None)
                    if nav_to:
                        for sid_t, rq in idx.read_query_by_app.get(aconn, {}).items():
                            if rq == nav_to:
                                reached.add((aconn, sid_t))
        for app, smap in (getattr(screens, "screens", None) or {}).items():
            for sid in smap:
                if (app, sid) not in reached:
                    out.append(Issue("warning", "Orphan screen",
                                     f"{app}.{sid} is not reachable from any menu or drill", _screen_deep_link(app, sid)))


def _check_unused_queries(state: Any, idx: _Index, out: list[Issue]) -> None:
    """A connector query (table CRUD slot / custom query / lookup / sequence source) that
    nothing in the loaded config references is dead weight — the canonical case being a
    lookup-backing query left behind after its dictionary lookup was repointed at another
    connector. Reuses :func:`_find_query_usages` — the exact reverse-reference walk Find usages
    shows — so the report stays consistent with it: a query flagged here is one Find usages would
    also report as "0 references — safe to delete". That walk covers every config reference path
    (screens incl. column groups + embedded nested forms, dialog tabs, actions, menus, dictionary
    lookups/sequences, charts, dashboards, exports), so this is reliable, not a guess.

    Warning-level, not error: the query is still valid SQL and harmless to keep — it's dead weight
    to clean up, not a broken reference."""
    for conn in sorted(idx.sql_by_conn.keys()):
        for q in sorted(idx.sql_by_conn.get(conn, set())):
            if not _find_query_usages(state, conn, q):
                out.append(Issue(
                    "warning", "Unused query",
                    f"query '{conn}.{q}' is referenced by nothing in the loaded config",
                    _connector_query_deep_link(conn, q),
                ))


def check_integrity(state: Any) -> list[Issue]:
    """Run every check against the loaded config and return the issues (errors first, then
    warnings; stable within each by category + message)."""
    idx = _build_index(state)
    out: list[Issue] = []
    _check_menus(state, idx, out)
    _check_screens(state, idx, out)
    _check_shared_actions(state, idx, out)
    _check_dictionary(state, idx, out)
    _check_dashboards_charts(state, idx, out)
    _check_homes(state, idx, out)
    _check_unused_connectors(state, idx, out)
    _check_orphan_screens(state, idx, out)
    _check_unused_queries(state, idx, out)
    out.sort(key=lambda i: (0 if i.severity == "error" else 1, i.category, i.message))
    return out
