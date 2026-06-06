"""Find-usages — the reference graph across loaded configs.

Powers ``POST /admin/find-usages``. Given ``{kind, name, scope?}``, walks the live
registries already in ``app.state`` (connectors, screens, menus, dashboards, charts,
jobs, dictionary) and returns a structured list of every place that references the
named entity. Used by every Settings editor to surface "where is this used?" before
a rename / delete — and to spot orphans quickly.

The walker NEVER reads from disk. It operates on the already-validated, in-memory
config objects — so usage results reflect the same state the runtime serves, not
whatever's on disk after a half-saved edit. Run /admin/reload first if disk + memory
have drifted.

A ``Usage`` carries enough context for the chat UI / editors to:
  - display a human-readable line (``label``),
  - route to the editor that owns the referencing config (``deep_link``),
  - and grouping (``type`` — column / prompt / lookup / etc.).

Kinds supported:
  dictionary_entry  — scope = "shared" or "<connector>"
  lookup            — scope = "shared" or "<connector>"
  sequence          — scope = "shared" or "<connector>"
  enum              — scope = "shared" or "<connector>"
  query             — scope = "<connector>"   (the connector that owns the query)
  api_endpoint      — scope = "<connector>"   (the API connector that owns the endpoint)
  connector         — scope unused
  screen            — scope = "<app>"
  menu_item         — scope = "<app>"
  pool              — scope unused
  chart             — scope = "<connector>"
  dashboard         — scope unused (dashboards are top-level)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


UsageType = Literal[
    # dictionary_entry — references to DD ids
    "screen_column_dd",         # ColumnHint.dd in a screen
    "connector_column_dd",      # ColumnHint.dd in a connector's QueryDef.columns
    "prompt_field_dd",          # PromptField.dd inside an action's prompt_fields
    "sequence_dd_id",           # SequenceDef.dd_id
    "lookup_return_param",      # LookupDef.return_params[]
    # lookup / sequence / enum references
    "entry_rules_values",       # DictionaryEntry.rules_values pointing at this lookup / sequence / enum
    # query references
    "screen_read_query",        # Screen.read_query
    "screen_update_query",      # Screen.update_query
    "screen_insert_query",      # Screen.insert_query
    "screen_delete_query",      # Screen.delete_query
    "nested_form_read_query",   # NestedFormTab.read_query (dialog tab OR FormTab.nested_forms)
    "nested_form_update_query", # NestedFormTab.update_query
    "nested_form_insert_query", # NestedFormTab.insert_query
    "nested_form_delete_query", # NestedFormTab.delete_query (child cleanup on parent delete)
    "column_group_update_query",# ColumnGroup.update_query (1:1 related-table write-back)
    "column_group_insert_query",# ColumnGroup.insert_query
    "column_group_delete_query",# ColumnGroup.delete_query
    "action_run_query",         # RunQueryAction.query
    "action_navigate_to",       # NavigateAction.to
    "lookup_query",             # LookupDef.query
    "sequence_query",           # SequenceDef.query
    "sequence_previous_query",  # SequenceDef.previous_query
    "export_sheet_query",       # SheetSpec.query
    "chart_query",              # ChartConfig.query
    "dashboard_widget_query",   # Widget.query (chart / kpi / table / select widgets)
    "nomaflow_step_query",      # nomaflow SQL step's query field
    "menu_target_query",        # MenuItem.target (a query leaf) pointing at this query
    # connector references
    "screen_connector",         # Screen.connector
    "nested_tab_connector",     # NestedFormTab / NestedTableTab .connector
    "action_connector",         # RunQueryAction.connector / CallApiAction.connector / NavigateAction.connector
    "lookup_connector",         # LookupDef.connector
    "sequence_connector",       # SequenceDef.connector
    "menu_connector",           # MenuItem.connector
    "chart_connector",          # ChartConfig.connector (scope keys are connectors but the field is the same)
    "dashboard_widget_connector", # Widget.connector
    "dashboard_connector",      # Dashboard.connector
    "export_sheet_connector",   # SheetSpec.connector
    "nomaflow_step_connector",  # SQL step's connector
    # screen references
    "menu_target_screen",       # MenuItem.target where MenuItem.type matches a screen target
    "nested_table_screen",      # NestedTableTab.screen
    "action_navigate_screen",   # (currently navigation is via query, not screen — kept for future)
    # api_endpoint references
    "action_api_call",          # CallApiAction.endpoint
    # pool references
    "connector_pool",           # Connector.pool
    # menu_item references
    "connector_home",           # Connector.home → menu item id
    # chart references
    "screen_chart_id",          # Screen.chart_id
    "dashboard_widget_chart",   # ChartWidget referencing a saved chart by id
]


@dataclass(slots=True)
class Usage:
    """One reference to the queried entity. The ``label`` line is what the UI shows;
    ``deep_link`` carries enough info for the frontend to route to the right editor
    with the right selection."""

    type: str
    label: str
    # Where to navigate when the operator clicks. Shape matches the cross-link query
    # params the frontend already uses (e.g. /settings?tab=screens&app=...&screen=...).
    deep_link: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "label": self.label, "deep_link": self.deep_link}


# ── walkers ──────────────────────────────────────────────────────────────────────────────

def _iter_actions(action_list: Any) -> Any:
    """Walk an action list recursively — chains, IF/LOOP branches, on_success / on_error."""
    if not action_list:
        return
    for action in action_list:
        yield action
        # Chain actions have a `steps` field; IF actions have `if_true` + `if_false`; LOOP has `body`.
        for child_attr in ("steps", "if_true", "if_false", "body", "on_success", "on_error"):
            child = getattr(action, child_attr, None)
            if child:
                yield from _iter_actions(child)


def _iter_screen_actions(screen: Any) -> Any:
    """Every action attached to a screen — top-level + per-tab + dialog hooks. Yields
    (where_label, action) so the resulting usage label can describe the location."""
    for action in _iter_actions(getattr(screen, "actions", None) or []):
        yield ("screen.actions", action)
    for hook in ("on_insert", "on_update", "on_delete", "row_menu"):
        for action in _iter_actions(getattr(screen, hook, None) or []):
            yield (f"screen.{hook}", action)
    dialog = getattr(screen, "dialog", None)
    if dialog is not None:
        for hook in ("on_load", "on_save", "on_cancel"):
            for action in _iter_actions(getattr(dialog, hook, None) or []):
                yield (f"dialog.{hook}", action)
        for tab in getattr(dialog, "tabs", None) or []:
            for action in _iter_actions(getattr(tab, "actions", None) or []):
                tab_id = getattr(tab, "id", "?")
                yield (f"tab[{tab_id}].actions", action)


def _iter_prompt_fields(action: Any) -> Any:
    """Yield every PromptField on an action (prompt_fields is on PromptableMixin actions)."""
    for pf in getattr(action, "prompt_fields", None) or []:
        yield pf


def _screen_deep_link(app: str, screen_id: str) -> dict[str, Any]:
    return {"editor": "screens", "app": app, "screen": screen_id}


def _connector_deep_link(name: str) -> dict[str, Any]:
    return {"editor": "connectors", "connector": name}


def _connector_query_deep_link(connector: str, query: str) -> dict[str, Any]:
    return {"editor": "connectors", "connector": connector, "query": query}


def _dict_deep_link(kind: str, scope: str, name: str) -> dict[str, Any]:
    return {"editor": "dictionary", "kind": kind, "scope": scope, "name": name}


def _menu_deep_link(app: str, item_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"editor": "menus", "app": app}
    if item_id:
        out["item"] = item_id
    return out


def _chart_deep_link(scope: str, chart_id: str) -> dict[str, Any]:
    return {"editor": "charts", "scope": scope, "chart": chart_id}


def _dashboard_deep_link(dashboard_id: str) -> dict[str, Any]:
    return {"editor": "dashboards", "dashboard": dashboard_id}


# ── dictionary entry usages ─────────────────────────────────────────────────────────────

def _find_dd_usages(state: Any, name: str, scope: str) -> list[Usage]:
    """Find every reference to dictionary entry *name* under *scope* (``"shared"`` or
    a connector). Walks: connector column hints, screen column hints + prompt fields,
    sequence dd_id, lookup return_params (within the same scope)."""
    out: list[Usage] = []
    name_u = name.upper()

    # 1) Connector column hints (`QueryDef.columns[].dd`).
    connectors = getattr(state, "connectors", None)
    if connectors is not None:
        for cname, conn in (_iter_connectors(connectors)):
            if scope != "shared" and cname != scope:
                continue
            for q in getattr(getattr(conn, "config", None), "queries", None) or []:
                for col in getattr(q, "columns", None) or []:
                    if (col.dd or col.name or "").upper() == name_u:
                        out.append(Usage(
                            type="connector_column_dd",
                            label=f"{cname} · {q.name} · column {col.name}",
                            deep_link=_connector_query_deep_link(cname, q.name),
                        ))

    # 2) Screens — ColumnHint.dd on Screen.columns + PromptField.dd inside any action's prompt_fields.
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
            for sid, screen in screen_map.items():
                # Screen.columns
                for col in getattr(screen, "columns", None) or []:
                    if (col.dd or col.name or "").upper() == name_u:
                        out.append(Usage(
                            type="screen_column_dd",
                            label=f"{app}.{sid} · column {col.name}",
                            deep_link=_screen_deep_link(app, sid),
                        ))
                # Action prompt fields (top-level + per-tab + hooks)
                for where, action in _iter_screen_actions(screen):
                    for pf in _iter_prompt_fields(action):
                        if (getattr(pf, "dd", None) or getattr(pf, "name", "") or "").upper() == name_u:
                            out.append(Usage(
                                type="prompt_field_dd",
                                label=f"{app}.{sid} · {where} · prompt {pf.name}",
                                deep_link=_screen_deep_link(app, sid),
                            ))
                # Nested-form tabs carry their own ScreenField list (not ColumnHint, but they
                # reference dictionary entries via the field name) — those resolve through
                # the parent screen's columns, so they're already covered. Skip.

    # 3) Dictionary internal refs — SequenceDef.dd_id + LookupDef.return_params.
    dictionary = _get_dictionary(state)
    if dictionary is not None:
        for s, section in _iter_dict_sections(dictionary):
            if scope == "shared" and s != "shared":
                # A shared entry can still be referenced from a per-connector overlay's lookups /
                # sequences — those reference rows in the same scope they live in. Skip cross-scope.
                continue
            if scope != "shared" and s != scope:
                continue
            for seq_id, seq in (section.get("sequences") or {}).items():
                if (getattr(seq, "dd_id", None) or "").upper() == name_u:
                    out.append(Usage(
                        type="sequence_dd_id",
                        label=f"{s} · sequence {seq_id} · dd_id",
                        deep_link=_dict_deep_link("sequences", s, seq_id),
                    ))
            for lk_id, lk in (section.get("lookups") or {}).items():
                for rp in getattr(lk, "return_params", None) or []:
                    if (rp or "").upper() == name_u:
                        out.append(Usage(
                            type="lookup_return_param",
                            label=f"{s} · lookup {lk_id} · return_param",
                            deep_link=_dict_deep_link("lookups", s, lk_id),
                        ))
    return out


# ── lookup / sequence / enum usages ─────────────────────────────────────────────────────

_RULES_FOR_KIND = {
    "lookup": {"LOOKUP"},
    "sequence": {"SEQUENCE", "NN"},
    "enum": {"ENUM"},
}


def _find_rule_target_usages(state: Any, kind: str, name: str, scope: str) -> list[Usage]:
    """Find DictionaryEntry.rules_values references to this lookup / sequence / enum
    within the same scope, PLUS transitive screen / connector references those entries
    pull in (a screen with ``ColumnHint.dd = "USR_ID"`` where the USR_ID entry has
    ``rules = "LOOKUP", rules_values = "1"`` is using lookup ``1`` indirectly — the
    operator wants to see that chain when they rename / delete the lookup).

    Cross-scope DD references aren't followed — operators may carry two different
    lookups with the same id in two scopes."""
    accept = _RULES_FOR_KIND.get(kind) or set()
    out: list[Usage] = []
    dictionary = _get_dictionary(state)
    if dictionary is None:
        return out
    # Pass 1 — direct references (DD entries pointing at this lookup / sequence / enum).
    referencing_entries: list[tuple[str, str]] = []  # (scope, entry_id) — used by pass 2
    for s, section in _iter_dict_sections(dictionary):
        if scope == "shared" and s != "shared":
            continue
        if scope != "shared" and s != scope:
            continue
        for eid, entry in (section.get("entries") or {}).items():
            rules = (getattr(entry, "rules", None) or "").upper()
            if rules in accept and (getattr(entry, "rules_values", None) or "") == name:
                referencing_entries.append((s, eid))
                out.append(Usage(
                    type="entry_rules_values",
                    label=f"{s} · entry {eid} · rules={rules}",
                    deep_link=_dict_deep_link("entries", s, eid),
                ))
    # Pass 2 — transitive: for each referencing entry, pull in everything that references
    # it (screens, connector column hints, prompt fields, other sequences / lookups). The
    # result label is prefixed with "via entry <id>" so the chain is legible.
    for s, eid in referencing_entries:
        for u in _find_dd_usages(state, eid, s):
            # Skip the dictionary-internal links we already surfaced in pass 1 to avoid duplicates.
            if u.type in ("sequence_dd_id", "lookup_return_param"):
                continue
            out.append(Usage(
                type=u.type,
                label=f"via {eid} → {u.label}",
                deep_link=u.deep_link,
            ))
    return out


# ── query usages ────────────────────────────────────────────────────────────────────────

def _find_query_usages(state: Any, connector: str, query: str) -> list[Usage]:
    """Find every reference to ``<connector>.<query>``. A query reference always carries a
    connector hint (sometimes ``None`` meaning "the parent's connector") — we treat a
    ``None`` connector as matching the entity's surrounding default."""
    out: list[Usage] = []

    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
            for sid, screen in screen_map.items():
                # The screen's own connector is either explicit (Screen.connector) or the app default
                # (= the connector named ``app``, by convention). We resolve here so a None on an
                # inner field still matches.
                screen_conn = screen.connector or app
                for attr, kind in (
                    ("read_query", "screen_read_query"),
                    ("update_query", "screen_update_query"),
                    ("insert_query", "screen_insert_query"),
                    ("delete_query", "screen_delete_query"),
                ):
                    v = getattr(screen, attr, None)
                    if v == query and screen_conn == connector:
                        out.append(Usage(
                            type=kind,
                            label=f"{app}.{sid} · {attr}",
                            deep_link=_screen_deep_link(app, sid),
                        ))
                # Nested tabs reference queries with their own connector default.
                dialog = getattr(screen, "dialog", None)
                if dialog is not None:
                    for tab in getattr(dialog, "tabs", None) or []:
                        tab_conn = getattr(tab, "connector", None) or screen_conn
                        for attr, kind in (
                            ("read_query", "nested_form_read_query"),
                            ("update_query", "nested_form_update_query"),
                            ("insert_query", "nested_form_insert_query"),
                        ):
                            v = getattr(tab, attr, None)
                            if v == query and tab_conn == connector:
                                out.append(Usage(
                                    type=kind,
                                    label=f"{app}.{sid} · tab[{tab.id}].{attr}",
                                    deep_link=_screen_deep_link(app, sid),
                                ))
                        # A FormTab can EMBED nested forms (``FormTab.nested_forms``) — each a
                        # NestedFormTab with its own read/update/insert queries + connector. The
                        # loop above only sees tabs that *are* nested forms; these are nested
                        # *inside* a form tab, so they need an explicit walk (integrity validates
                        # them too — see _check_screens). Without this a query referenced only by an
                        # embedded form looks unused.
                        for nf in getattr(tab, "nested_forms", None) or []:
                            nf_conn = getattr(nf, "connector", None) or tab_conn
                            for attr, kind in (
                                ("read_query", "nested_form_read_query"),
                                ("update_query", "nested_form_update_query"),
                                ("insert_query", "nested_form_insert_query"),
                                ("delete_query", "nested_form_delete_query"),
                            ):
                                if getattr(nf, attr, None) == query and nf_conn == connector:
                                    out.append(Usage(
                                        type=kind,
                                        label=f"{app}.{sid} · tab[{tab.id}] · form[{getattr(nf, 'id', '?')}].{attr}",
                                        deep_link=_screen_deep_link(app, sid),
                                    ))
                # Column groups — a 1:1 related table edited inline and written back through the
                # group's own update / insert / delete queries (``ColumnGroup``). Effective connector
                # is the group's own, else the screen's. Integrity validates these (``_check_screens``);
                # usages must see them too — else a query referenced ONLY by a column group (e.g.
                # ``f00921`` from ``f0092``'s columns) shows 0 references and looks safe to delete.
                for grp in getattr(screen, "column_groups", None) or []:
                    g_conn = getattr(grp, "connector", None) or screen_conn
                    if g_conn != connector:
                        continue
                    for attr, kind in (
                        ("update_query", "column_group_update_query"),
                        ("insert_query", "column_group_insert_query"),
                        ("delete_query", "column_group_delete_query"),
                    ):
                        if getattr(grp, attr, None) == query:
                            out.append(Usage(
                                type=kind,
                                label=f"{app}.{sid} · group[{getattr(grp, 'id', '?')}].{attr}",
                                deep_link=_screen_deep_link(app, sid),
                            ))
                # Actions — run_query (`query`) + navigate (`to`).
                for where, action in _iter_screen_actions(screen):
                    a_type = getattr(action, "type", None)
                    a_conn = getattr(action, "connector", None) or screen_conn
                    if a_type == "run_query" and getattr(action, "query", None) == query and a_conn == connector:
                        out.append(Usage(
                            type="action_run_query",
                            label=f"{app}.{sid} · {where} · run_query",
                            deep_link=_screen_deep_link(app, sid),
                        ))
                    if a_type == "navigate" and getattr(action, "to", None) == query and a_conn == connector:
                        out.append(Usage(
                            type="action_navigate_to",
                            label=f"{app}.{sid} · {where} · navigate to",
                            deep_link=_screen_deep_link(app, sid),
                        ))
                # Excel export sheets.
                export = getattr(screen, "export", None)
                if export is not None:
                    for sheet in getattr(export, "sheets", None) or []:
                        s_conn = getattr(sheet, "connector", None) or screen_conn
                        if getattr(sheet, "query", None) == query and s_conn == connector:
                            out.append(Usage(
                                type="export_sheet_query",
                                label=f"{app}.{sid} · export sheet",
                                deep_link=_screen_deep_link(app, sid),
                            ))

    # Menus pointing at this query — a ``query`` leaf's ``target`` is a query name on the item's
    # connector (defaults to the menu app). This is how a menu opens a table view / screen, so a
    # query that nothing else references is still "in use" if a menu links to it.
    menus = getattr(state, "menus", None)
    if menus is not None:
        menu_map = menus.menus if hasattr(menus, "menus") else menus
        for menu_app, app_menu in (menu_map or {}).items():
            for item in getattr(app_menu, "items", None) or []:
                # A ``query`` leaf targets a query name directly. (A ``screen`` leaf targets a
                # screen id, not a query — those surface under the screen's usages instead.)
                if getattr(item, "type", None) != "query":
                    continue
                item_conn = getattr(item, "connector", None) or menu_app
                if getattr(item, "target", None) == query and item_conn == connector:
                    out.append(Usage(
                        type="menu_target_query",
                        label=f"{menu_app} · {item.id} → {item.target}",
                        deep_link=_menu_deep_link(menu_app, item.id),
                    ))

    # Lookups + sequences (within the connector's scope OR shared).
    dictionary = _get_dictionary(state)
    if dictionary is not None:
        for s, section in _iter_dict_sections(dictionary):
            # A lookup/sequence's `query` defaults to the section's connector when None.
            section_conn = None if s == "shared" else s
            for lk_id, lk in (section.get("lookups") or {}).items():
                lk_conn = getattr(lk, "connector", None) or section_conn
                if getattr(lk, "query", None) == query and lk_conn == connector:
                    out.append(Usage(
                        type="lookup_query",
                        label=f"{s} · lookup {lk_id}",
                        deep_link=_dict_deep_link("lookups", s, lk_id),
                    ))
            for seq_id, seq in (section.get("sequences") or {}).items():
                seq_conn = getattr(seq, "connector", None) or section_conn
                if getattr(seq, "query", None) == query and seq_conn == connector:
                    out.append(Usage(
                        type="sequence_query",
                        label=f"{s} · sequence {seq_id}",
                        deep_link=_dict_deep_link("sequences", s, seq_id),
                    ))
                if getattr(seq, "previous_query", None) == query and seq_conn == connector:
                    out.append(Usage(
                        type="sequence_previous_query",
                        label=f"{s} · sequence {seq_id} (previous_query)",
                        deep_link=_dict_deep_link("sequences", s, seq_id),
                    ))

    # Charts + dashboards.
    charts = getattr(state, "charts", None)
    if charts is not None and hasattr(charts, "charts"):
        for scope_name, chart_map in (charts.charts or {}).items():
            for cid, chart in chart_map.items():
                if getattr(chart, "connector", None) == connector and getattr(chart, "query", None) == query:
                    out.append(Usage(
                        type="chart_query",
                        label=f"{scope_name} · chart {cid}",
                        deep_link=_chart_deep_link(scope_name, cid),
                    ))
    dashboards = getattr(state, "dashboards", None)
    if dashboards is not None and hasattr(dashboards, "dashboards"):
        # dashboards.dashboards is { scope: { id: Dashboard } } in the scoped form.
        for scope_name, dash_map in (dashboards.dashboards or {}).items():
            if not isinstance(dash_map, dict):
                continue
            for did, dash in dash_map.items():
                dash_conn = getattr(dash, "connector", None)
                for w in getattr(dash, "widgets", None) or []:
                    w_conn = getattr(w, "connector", None) or dash_conn
                    w_query = getattr(w, "query", None)
                    if w_conn == connector and w_query == query:
                        out.append(Usage(
                            type="dashboard_widget_query",
                            label=f"{scope_name} · dashboard {did} · {getattr(w, 'type', 'widget')}",
                            deep_link=_dashboard_deep_link(did),
                        ))
    return out


# ── connector usages ─────────────────────────────────────────────────────────────────────

def _find_connector_usages(state: Any, name: str) -> list[Usage]:
    out: list[Usage] = []
    # Screens
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
            for sid, screen in screen_map.items():
                if (getattr(screen, "connector", None) or app) == name:
                    out.append(Usage(
                        type="screen_connector",
                        label=f"{app}.{sid}",
                        deep_link=_screen_deep_link(app, sid),
                    ))
                dialog = getattr(screen, "dialog", None)
                if dialog is not None:
                    for tab in getattr(dialog, "tabs", None) or []:
                        if getattr(tab, "connector", None) == name:
                            out.append(Usage(
                                type="nested_tab_connector",
                                label=f"{app}.{sid} · tab[{tab.id}]",
                                deep_link=_screen_deep_link(app, sid),
                            ))
                for where, action in _iter_screen_actions(screen):
                    if getattr(action, "connector", None) == name:
                        out.append(Usage(
                            type="action_connector",
                            label=f"{app}.{sid} · {where} · {getattr(action, 'type', '?')}",
                            deep_link=_screen_deep_link(app, sid),
                        ))
    # Menus
    menus = getattr(state, "menus", None)
    if menus is not None:
        menu_map = menus.menus if hasattr(menus, "menus") else menus
        for app, app_menu in (menu_map or {}).items():
            for item in getattr(app_menu, "items", None) or []:
                if getattr(item, "connector", None) == name:
                    out.append(Usage(
                        type="menu_connector",
                        label=f"{app} · {item.id}",
                        deep_link=_menu_deep_link(app, item.id),
                    ))
    # Dictionary — lookups + sequences may pin connector explicitly.
    dictionary = _get_dictionary(state)
    if dictionary is not None:
        for s, section in _iter_dict_sections(dictionary):
            for lk_id, lk in (section.get("lookups") or {}).items():
                if getattr(lk, "connector", None) == name:
                    out.append(Usage(
                        type="lookup_connector",
                        label=f"{s} · lookup {lk_id}",
                        deep_link=_dict_deep_link("lookups", s, lk_id),
                    ))
            for seq_id, seq in (section.get("sequences") or {}).items():
                if getattr(seq, "connector", None) == name:
                    out.append(Usage(
                        type="sequence_connector",
                        label=f"{s} · sequence {seq_id}",
                        deep_link=_dict_deep_link("sequences", s, seq_id),
                    ))
    # Charts — keyed under their connector scope; chart.connector is set explicitly.
    charts = getattr(state, "charts", None)
    if charts is not None and hasattr(charts, "charts"):
        for scope_name, chart_map in (charts.charts or {}).items():
            for cid, chart in chart_map.items():
                if getattr(chart, "connector", None) == name:
                    out.append(Usage(
                        type="chart_connector",
                        label=f"{scope_name} · chart {cid}",
                        deep_link=_chart_deep_link(scope_name, cid),
                    ))
    # Dashboards — each widget plus the dashboard's own connector.
    dashboards = getattr(state, "dashboards", None)
    if dashboards is not None and hasattr(dashboards, "dashboards"):
        for scope_name, dash_map in (dashboards.dashboards or {}).items():
            if not isinstance(dash_map, dict):
                continue
            for did, dash in dash_map.items():
                if getattr(dash, "connector", None) == name:
                    out.append(Usage(
                        type="dashboard_connector",
                        label=f"{scope_name} · dashboard {did}",
                        deep_link=_dashboard_deep_link(did),
                    ))
                for w in getattr(dash, "widgets", None) or []:
                    if getattr(w, "connector", None) == name:
                        out.append(Usage(
                            type="dashboard_widget_connector",
                            label=f"{scope_name} · dashboard {did} · {getattr(w, 'type', 'widget')}",
                            deep_link=_dashboard_deep_link(did),
                        ))
    return out


# ── api endpoint usages ──────────────────────────────────────────────────────────────────

def _find_api_endpoint_usages(state: Any, connector: str, endpoint: str) -> list[Usage]:
    out: list[Usage] = []
    screens = getattr(state, "screens", None)
    if screens is None:
        return out
    for app, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
        for sid, screen in screen_map.items():
            for where, action in _iter_screen_actions(screen):
                if (getattr(action, "type", None) == "call_api"
                        and getattr(action, "connector", None) == connector
                        and getattr(action, "endpoint", None) == endpoint):
                    out.append(Usage(
                        type="action_api_call",
                        label=f"{app}.{sid} · {where} · call_api",
                        deep_link=_screen_deep_link(app, sid),
                    ))
    return out


# ── screen usages ────────────────────────────────────────────────────────────────────────

def _find_screen_usages(state: Any, app: str, screen_id: str) -> list[Usage]:
    out: list[Usage] = []
    # Resolve this screen's read query + effective connector. A menu opens a screen by linking to
    # its read query (e.g. ``f0004_get``), NOT the screen id — so to find the menus that open THIS
    # screen we match the menu target against the read query, on the screen's connector. We also
    # keep the direct ``target == screen_id`` match for any future screen-id menu target.
    screens = getattr(state, "screens", None)
    all_screens = (screens.screens if hasattr(screens, "screens") else {}) if screens is not None else {}
    app_screens = all_screens.get(app) if isinstance(all_screens, dict) else None
    screen_obj = app_screens.get(screen_id) if isinstance(app_screens, dict) else None
    read_query = getattr(screen_obj, "read_query", None)
    screen_conn = getattr(screen_obj, "connector", None) or app
    # Menus pointing at this screen — either directly by screen id, or (the common case) via the
    # screen's read query on the screen's connector.
    menus = getattr(state, "menus", None)
    if menus is not None:
        menu_map = menus.menus if hasattr(menus, "menus") else menus
        for menu_app, app_menu in (menu_map or {}).items():
            for item in getattr(app_menu, "items", None) or []:
                target = getattr(item, "target", None)
                item_conn = getattr(item, "connector", None) or menu_app
                direct = target == screen_id and item_conn == app
                via_query = read_query is not None and target == read_query and item_conn == screen_conn
                if direct or via_query:
                    out.append(Usage(
                        type="menu_target_screen",
                        label=f"{menu_app} · {item.id} → {target}",
                        deep_link=_menu_deep_link(menu_app, item.id),
                    ))
    # NestedTableTab.screen — a parent screen embedding this one — and navigate actions that pin
    # this screen (NavigateAction.screen) so a drill opens it specifically.
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app_p, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
            for sid_p, screen in screen_map.items():
                dialog = getattr(screen, "dialog", None)
                if dialog is not None:
                    for tab in getattr(dialog, "tabs", None) or []:
                        if getattr(tab, "type", None) == "nested_table" and getattr(tab, "screen", None) == screen_id:
                            # Nested-table screens are keyed by id only — they're resolved against
                            # the parent screen's app. So a match in any app is a real reference.
                            if app_p == app:
                                out.append(Usage(
                                    type="nested_table_screen",
                                    label=f"{app_p}.{sid_p} · tab[{tab.id}]",
                                    deep_link=_screen_deep_link(app_p, sid_p),
                                ))
                # NavigateAction.screen — a row-menu / toolbar drill that pins THIS screen.
                firing_conn = getattr(screen, "connector", None) or app_p
                for where, action in _iter_screen_actions(screen):
                    if getattr(action, "type", None) != "navigate" or getattr(action, "screen", None) != screen_id:
                        continue
                    nav_conn = getattr(action, "connector", None) or firing_conn
                    if nav_conn == screen_conn:
                        out.append(Usage(
                            type="action_navigate_screen",
                            label=f"{app_p}.{sid_p} · {where} · navigate",
                            deep_link=_screen_deep_link(app_p, sid_p),
                        ))
    return out


# ── menu item usages ─────────────────────────────────────────────────────────────────────

def _find_menu_item_usages(state: Any, app: str, item_id: str) -> list[Usage]:
    out: list[Usage] = []
    # Connector.home — points at a menu item id by convention (within the connector's own app).
    connectors = getattr(state, "connectors", None)
    if connectors is not None:
        for cname, conn in (_iter_connectors(connectors)):
            cfg = getattr(conn, "config", None)
            home = getattr(cfg, "home", None) if cfg is not None else None
            if home == item_id and cname == app:
                out.append(Usage(
                    type="connector_home",
                    label=f"{cname} · home",
                    deep_link=_connector_deep_link(cname),
                ))
    # Child items pointing to this id as parent.
    menus = getattr(state, "menus", None)
    if menus is not None:
        menu_map = menus.menus if hasattr(menus, "menus") else menus
        app_menu = (menu_map or {}).get(app) if isinstance(menu_map, dict) else None
        if app_menu is not None:
            for item in getattr(app_menu, "items", None) or []:
                if getattr(item, "parent", None) == item_id:
                    out.append(Usage(
                        type="menu_target_screen",  # parent-of relationship, reuses the menu deep-link
                        label=f"{app} · {item.id} (child of {item_id})",
                        deep_link=_menu_deep_link(app, item.id),
                    ))
    return out


# ── pool usages ──────────────────────────────────────────────────────────────────────────

def _find_pool_usages(state: Any, name: str) -> list[Usage]:
    out: list[Usage] = []
    connectors = getattr(state, "connectors", None)
    if connectors is None:
        return out
    for cname, conn in (_iter_connectors(connectors)):
        cfg = getattr(conn, "config", None)
        pool = getattr(cfg, "pool", None) if cfg is not None else None
        if pool == name:
            out.append(Usage(
                type="connector_pool",
                label=f"{cname} · pool",
                deep_link=_connector_deep_link(cname),
            ))
    return out


# ── chart usages ─────────────────────────────────────────────────────────────────────────

def _find_chart_usages(state: Any, connector: str, chart_id: str) -> list[Usage]:
    out: list[Usage] = []
    # Screen.chart_id pre-fills the chart view.
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, screen_map in (screens.screens if hasattr(screens, "screens") else {}).items():
            for sid, screen in screen_map.items():
                # Screen.chart_id is a bare id (no scope) — but charts are now scoped under their
                # connector, and a screen's chart_id resolves against its own connector. Match
                # both id + scope = the screen's connector.
                screen_conn = screen.connector or app
                if getattr(screen, "chart_id", None) == chart_id and screen_conn == connector:
                    out.append(Usage(
                        type="screen_chart_id",
                        label=f"{app}.{sid} · chart_id",
                        deep_link=_screen_deep_link(app, sid),
                    ))
    # Dashboard widgets that reference a chart by id (chart-ref widget).
    dashboards = getattr(state, "dashboards", None)
    if dashboards is not None and hasattr(dashboards, "dashboards"):
        for scope_name, dash_map in (dashboards.dashboards or {}).items():
            if not isinstance(dash_map, dict):
                continue
            for did, dash in dash_map.items():
                dash_conn = getattr(dash, "connector", None)
                for w in getattr(dash, "widgets", None) or []:
                    w_conn = getattr(w, "connector", None) or dash_conn
                    # chart-ref widgets use `chart_id` to point at a saved chart.
                    if getattr(w, "chart_id", None) == chart_id and w_conn == connector:
                        out.append(Usage(
                            type="dashboard_widget_chart",
                            label=f"{scope_name} · dashboard {did} · chart ref",
                            deep_link=_dashboard_deep_link(did),
                        ))
    return out


# ── helpers ──────────────────────────────────────────────────────────────────────────────

def _iter_connectors(registry: Any) -> Any:
    """Iterate (name, connector_instance) pairs from the ConnectorRegistry — no underscore poking."""
    if registry is None:
        return
    for name in registry.names():
        yield name, registry.get(name)


def _get_dictionary(state: Any) -> Any:
    """The active DictionaryFile (loaded by connector registry init). Returns None when not
    available (a bare deployment with no dictionary configured)."""
    connectors = getattr(state, "connectors", None)
    if connectors is None:
        return None
    return getattr(connectors, "dictionary", None)


def _iter_dict_sections(dictionary: Any) -> Any:
    """Yield ``(scope_label, {entries, lookups, sequences, enums})`` per scope — the
    top-level shared scope first, then each connector overlay. Section maps are dicts;
    missing collections come back as None for the caller to handle."""
    if dictionary is None:
        return
    yield ("shared", {
        "entries": getattr(dictionary, "entries", None),
        "lookups": getattr(dictionary, "lookups", None),
        "sequences": getattr(dictionary, "sequences", None),
        "enums": getattr(dictionary, "enums", None),
    })
    for scope, section in (getattr(dictionary, "connectors", None) or {}).items():
        yield (scope, {
            "entries": getattr(section, "entries", None),
            "lookups": getattr(section, "lookups", None),
            "sequences": getattr(section, "sequences", None),
            "enums": getattr(section, "enums", None),
        })


# ── dispatcher ───────────────────────────────────────────────────────────────────────────


def _find_action_usages(state: Any, name: str) -> list[Usage]:
    """Every reference to shared action *name* — a screen's ``call_action`` (any hook / tab / row
    menu) and another shared action whose steps call it (composition)."""
    out: list[Usage] = []
    screens = getattr(state, "screens", None)
    if screens is not None:
        for app, smap in (getattr(screens, "screens", None) or {}).items():
            for sid, screen in (smap or {}).items():
                for where, action in _iter_screen_actions(screen):
                    if getattr(action, "type", None) == "call_action" and getattr(action, "ref", None) == name:
                        out.append(Usage(
                            type="action_ref",
                            label=f"{app}.{sid} · {where}",
                            deep_link=_screen_deep_link(app, sid),
                        ))
    actions = getattr(state, "actions", None)
    if actions is not None:
        for aid, a in (getattr(actions, "actions", None) or {}).items():
            if aid == name:
                continue
            if any(
                getattr(act, "type", None) == "call_action" and getattr(act, "ref", None) == name
                for act in _iter_actions(getattr(a, "steps", None) or [])
            ):
                out.append(Usage(type="action_ref", label=f"action · {aid}", deep_link={"editor": "actions", "action": aid}))
    return out


def find_usages(state: Any, *, kind: str, name: str, scope: str | None = None) -> list[Usage]:
    """Main entry point. Returns every reference to ``(kind, name, scope)`` across the
    loaded configs. Empty list when nothing references it (useful: that means safe to
    rename / delete). Raises ``ValueError`` on an unknown kind."""
    if kind == "dictionary_entry":
        return _find_dd_usages(state, name, scope or "shared")
    if kind in ("lookup", "sequence", "enum"):
        return _find_rule_target_usages(state, kind, name, scope or "shared")
    if kind == "query":
        if not scope:
            raise ValueError("query lookups require scope = the connector name")
        return _find_query_usages(state, scope, name)
    if kind == "api_endpoint":
        if not scope:
            raise ValueError("api_endpoint lookups require scope = the connector name")
        return _find_api_endpoint_usages(state, scope, name)
    if kind == "connector":
        return _find_connector_usages(state, name)
    if kind == "screen":
        if not scope:
            raise ValueError("screen lookups require scope = the app name")
        return _find_screen_usages(state, scope, name)
    if kind == "menu_item":
        if not scope:
            raise ValueError("menu_item lookups require scope = the app name")
        return _find_menu_item_usages(state, scope, name)
    if kind == "pool":
        return _find_pool_usages(state, name)
    if kind == "chart":
        if not scope:
            raise ValueError("chart lookups require scope = the connector name")
        return _find_chart_usages(state, scope, name)
    if kind == "action":
        return _find_action_usages(state, name)
    raise ValueError(f"unknown kind: {kind!r}")
