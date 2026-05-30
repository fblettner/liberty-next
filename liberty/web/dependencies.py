"""Find-dependencies — the inverse of usages: given a set of seeds (typically screens
the operator wants to ship to another install), collect EVERYTHING those seeds reference
transitively, so the result is a self-contained config bundle.

Used by:
  * ``POST /admin/find-dependencies`` — returns the manifest for the Settings UI to render
    as a tree (operator inspects what would be packaged).
  * ``POST /admin/build-package`` — same walk, then serialises into a ZIP archive the
    operator can rsync to the target install and reload.

Design notes:
  * Closure traversal — worklist queue + visited set (keyed by ``(kind, scope, name)``) so
    nested-table cycles can't loop forever.
  * The walk produces typed records (``Dependency``) that the inspect / package paths share.
  * Secrets are NEVER serialised. ENC: ciphertexts pass through (they re-decrypt cleanly on
    the target if it carries the same master_key); env-var references (``${VAR}``) likewise.
    The package writer surfaces a warning listing the env vars the operator must set on the
    target.
  * nomaflow jobs are NOT followed from screen / dashboard seeds — the user manages job
    deployment separately (Per-job export is a separate path; see ``export_job``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ── seed + dependency record types ───────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class Seed:
    """One thing the operator selected. ``scope`` is the app for screens / menu items, the
    connector for charts, ``None`` for top-level kinds (dashboards, pools)."""

    kind: str
    name: str
    scope: str | None = None


@dataclass(slots=True)
class Dependency:
    """One entity in the closure. ``config`` is the parsed wire shape (Pydantic
    ``model_dump(exclude_defaults=True, exclude_none=True)``) — what would land in the
    matching TOML file. Secrets are NOT stripped here; the package writer handles that
    based on a list of well-known fields."""

    kind: str                       # screen / connector / pool / query / dictionary_entry / lookup / sequence / enum / chart / dashboard / menu_item / api_endpoint
    name: str                       # the entity id within its scope
    scope: str | None               # app for screens / menu items, connector for queries / charts / API endpoints, "shared" or connector for dictionary kinds, None for top-level
    config: dict[str, Any]
    # Where this dependency came from — useful in the inspect UI for "why is this here?".
    reasons: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str | None, str]:
        return (self.kind, self.scope, self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "name": self.name, "scope": self.scope,
            "config": self.config, "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class Manifest:
    """The complete dependency closure for a set of seeds — what would be packaged."""

    seeds: list[Seed]
    deps: list[Dependency]
    missing: list[dict[str, Any]] = field(default_factory=list)   # refs that didn't resolve (dangling chart_id, …)
    warnings: list[str] = field(default_factory=list)             # secrets stripped, env vars to set, …

    def by_kind(self) -> dict[str, list[Dependency]]:
        out: dict[str, list[Dependency]] = {}
        for d in self.deps:
            out.setdefault(d.kind, []).append(d)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeds": [{"kind": s.kind, "name": s.name, "scope": s.scope} for s in self.seeds],
            "deps": [d.to_dict() for d in self.deps],
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "by_kind": {k: [d.to_dict() for d in v] for k, v in self.by_kind().items()},
            "counts": {k: len(v) for k, v in self.by_kind().items()},
        }


# ── walker ───────────────────────────────────────────────────────────────────────────────


def collect_dependencies(state: Any, seeds: Iterable[Seed], *, max_depth: int = 50) -> Manifest:
    """Walk every reference from *seeds* outward, collecting the full closure. Cycle-safe
    (visited set keyed by ``(kind, scope, name)``)."""
    visited: set[tuple[str, str | None, str]] = set()
    deps: list[Dependency] = []
    missing: list[dict[str, Any]] = []
    warnings: list[str] = []

    # The work queue carries (Seed, reason). When we pull a seed, we resolve it to a Dependency
    # (or record missing), then enqueue every further ref it produces.
    work: list[tuple[Seed, str | None]] = [(s, None) for s in seeds]
    depth_guard = max(1, max_depth * 200)  # rough upper bound — protects against pathological config

    while work:
        depth_guard -= 1
        if depth_guard <= 0:
            warnings.append(f"dependency walk exceeded {max_depth * 200} iterations — partial result")
            break
        seed, reason = work.pop(0)
        key = (seed.kind, seed.scope, seed.name)
        if key in visited:
            # Already collected — just add the new reason if it's informative.
            if reason:
                for d in deps:
                    if d.key() == key and reason not in d.reasons:
                        d.reasons.append(reason)
                        break
            continue
        visited.add(key)

        added, refs = _resolve(state, seed)
        if added is None:
            missing.append({"kind": seed.kind, "name": seed.name, "scope": seed.scope, "reason": reason})
            continue
        if reason:
            added.reasons.append(reason)
        deps.append(added)
        for r_seed, r_reason in refs:
            work.append((r_seed, r_reason))

    return Manifest(seeds=list(seeds), deps=deps, missing=missing, warnings=warnings)


# ── per-kind resolvers ───────────────────────────────────────────────────────────────────


def _resolve(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    """Look up the entity for *seed* and return ``(Dependency, [(next_seed, reason), ...])``.
    Dependency is ``None`` when the entity can't be found (dangling reference)."""
    if seed.kind == "screen":
        return _resolve_screen(state, seed)
    if seed.kind == "menu_item":
        return _resolve_menu_item(state, seed)
    if seed.kind == "dashboard":
        return _resolve_dashboard(state, seed)
    if seed.kind == "connector":
        return _resolve_connector(state, seed)
    if seed.kind == "query":
        return _resolve_query(state, seed)
    if seed.kind == "api_endpoint":
        return _resolve_api_endpoint(state, seed)
    if seed.kind == "pool":
        return _resolve_pool(state, seed)
    if seed.kind == "dictionary_entry":
        return _resolve_dictionary_entry(state, seed)
    if seed.kind == "lookup":
        return _resolve_dict_target(state, seed, "lookups")
    if seed.kind == "sequence":
        return _resolve_dict_target(state, seed, "sequences")
    if seed.kind == "enum":
        return _resolve_dict_target(state, seed, "enums")
    if seed.kind == "chart":
        return _resolve_chart(state, seed)
    return None, []


# Helpers --------------------------------------------------------------------------------

def _dump(model: Any) -> dict[str, Any]:
    """Pydantic model → plain dict for the closure manifest.

    Defaults are KEPT (``exclude_defaults=False``) so the discriminator ``type`` fields
    on action variants (``run_query`` / ``navigate`` / ``call_api`` / …) — emitted by
    Pydantic as ``Literal[...]`` with the same value as the default — survive the
    round-trip. Dropping them would make the cloned / packaged config fail validation
    on the way back in with ``union_tag_not_found``: the import / clone-with-deps
    pipelines feed these dicts straight into ``ScreensFile.model_validate``.

    Same call shape :func:`liberty.web.admin.put_screens_parsed` uses for the same
    reason — see the long comment there about discriminator stripping."""
    return model.model_dump(exclude_defaults=False, exclude_none=True)


def _get_dictionary(state: Any) -> Any:
    cr = getattr(state, "connectors", None)
    return getattr(cr, "dictionary", None) if cr is not None else None


def _resolve_dd_via_column_name(state: Any, column_name: str, *, connector: str | None) -> Seed | None:
    """Resolve a column name to the dictionary entry that drives it. Mirrors the runtime's
    DictionaryFile.find_entry — per-connector overlay first, then shared."""
    d = _get_dictionary(state)
    if d is None:
        return None
    name_u = column_name.upper()
    if connector and connector in (d.connectors or {}):
        overlay = d.connectors[connector].entries or {}
        if name_u in overlay:
            return Seed(kind="dictionary_entry", name=name_u, scope=connector)
    if name_u in (d.entries or {}):
        return Seed(kind="dictionary_entry", name=name_u, scope="shared")
    return None


# Per-kind resolvers ---------------------------------------------------------------------

def _resolve_screen(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    screens = getattr(state, "screens", None)
    if screens is None or not seed.scope:
        return None, []
    screen_map = (screens.screens or {}).get(seed.scope) or {}
    screen = screen_map.get(seed.name)
    if screen is None:
        return None, []
    dep = Dependency(kind="screen", name=seed.name, scope=seed.scope, config=_dump(screen))
    refs: list[tuple[Seed, str]] = []
    eff_conn = getattr(screen, "connector", None) or seed.scope
    if eff_conn:
        refs.append((Seed("connector", eff_conn), f"screen {seed.scope}.{seed.name}"))
    # CRUD queries on the screen's connector.
    for attr in ("read_query", "update_query", "insert_query", "delete_query"):
        v = getattr(screen, attr, None)
        if v:
            refs.append((Seed("query", v, eff_conn), f"screen {seed.scope}.{seed.name}.{attr}"))
    # Column hints — each ColumnHint.dd (or its name) resolves to a DD entry.
    for col in getattr(screen, "columns", None) or []:
        dd_seed = _resolve_dd_via_column_name(state, col.dd or col.name, connector=eff_conn)
        if dd_seed:
            refs.append((dd_seed, f"screen {seed.scope}.{seed.name} column {col.name}"))
    # Row click → another screen.
    rc_screen = getattr(screen, "row_click_screen", None)
    if rc_screen:
        rc_conn = getattr(screen, "row_click_connector", None) or eff_conn
        rc_app = rc_conn or seed.scope
        refs.append((Seed("screen", rc_screen, rc_app), f"screen {seed.scope}.{seed.name} row_click"))
    # chart_id pre-fills the chart view.
    chart_id = getattr(screen, "chart_id", None)
    if chart_id:
        refs.append((Seed("chart", chart_id, eff_conn), f"screen {seed.scope}.{seed.name} chart_id"))
    # Dialog tabs (form / nested_form / nested_table) + their actions / fields.
    dialog = getattr(screen, "dialog", None)
    if dialog is not None:
        for tab in getattr(dialog, "tabs", None) or []:
            tab_type = getattr(tab, "type", None)
            tab_conn = getattr(tab, "connector", None) or eff_conn
            if tab_type == "nested_form":
                for attr in ("read_query", "update_query", "insert_query"):
                    v = getattr(tab, attr, None)
                    if v:
                        refs.append((Seed("query", v, tab_conn), f"screen {seed.scope}.{seed.name} tab[{tab.id}].{attr}"))
            elif tab_type == "nested_table":
                target = getattr(tab, "screen", None)
                if target:
                    refs.append((Seed("screen", target, seed.scope), f"screen {seed.scope}.{seed.name} nested_table[{tab.id}]"))
            # Action chains + their prompt_fields on every tab.
            for action in _iter_actions(getattr(tab, "actions", None) or []):
                refs.extend(_refs_from_action(action, state, eff_conn, where=f"{seed.scope}.{seed.name} tab[{tab.id}]"))
        # Dialog-level hooks.
        for hook in ("on_load", "on_save", "on_cancel"):
            for action in _iter_actions(getattr(dialog, hook, None) or []):
                refs.extend(_refs_from_action(action, state, eff_conn, where=f"{seed.scope}.{seed.name} dialog.{hook}"))
    # Top-level screen action hooks.
    for hook in ("actions", "on_insert", "on_update", "on_delete", "row_menu"):
        for action in _iter_actions(getattr(screen, hook, None) or []):
            refs.extend(_refs_from_action(action, state, eff_conn, where=f"{seed.scope}.{seed.name} {hook}"))
    # Excel export sheets.
    export = getattr(screen, "export", None)
    if export is not None:
        for sheet in getattr(export, "sheets", None) or []:
            s_conn = getattr(sheet, "connector", None) or eff_conn
            sq = getattr(sheet, "query", None)
            if sq:
                refs.append((Seed("query", sq, s_conn), f"screen {seed.scope}.{seed.name} export sheet"))
    return dep, refs


def _iter_actions(actions: Any) -> Any:
    if not actions:
        return
    for action in actions:
        yield action
        for child_attr in ("steps", "if_true", "if_false", "body", "on_success", "on_error"):
            child = getattr(action, child_attr, None)
            if child:
                yield from _iter_actions(child)


def _refs_from_action(action: Any, state: Any, default_conn: str | None, *, where: str) -> list[tuple[Seed, str]]:
    out: list[tuple[Seed, str]] = []
    a_type = getattr(action, "type", None)
    a_conn = getattr(action, "connector", None) or default_conn
    if a_type == "run_query":
        q = getattr(action, "query", None)
        if q and a_conn:
            out.append((Seed("query", q, a_conn), f"{where} run_query"))
    elif a_type == "navigate":
        q = getattr(action, "to", None)
        if q and a_conn:
            out.append((Seed("query", q, a_conn), f"{where} navigate"))
    elif a_type == "call_api":
        ep = getattr(action, "endpoint", None)
        if ep and a_conn:
            out.append((Seed("api_endpoint", ep, a_conn), f"{where} call_api"))
    # Prompt fields reference DD entries.
    for pf in getattr(action, "prompt_fields", None) or []:
        dd_seed = _resolve_dd_via_column_name(state, getattr(pf, "dd", None) or getattr(pf, "name", ""), connector=a_conn)
        if dd_seed:
            out.append((dd_seed, f"{where} prompt {pf.name}"))
    return out


def _resolve_menu_item(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    menus = getattr(state, "menus", None)
    if menus is None or not seed.scope:
        return None, []
    app_menu = (menus.menus or {}).get(seed.scope)
    if app_menu is None:
        return None, []
    item = next((it for it in (getattr(app_menu, "items", None) or []) if it.id == seed.name), None)
    if item is None:
        return None, []
    dep = Dependency(kind="menu_item", name=seed.name, scope=seed.scope, config=_dump(item))
    refs: list[tuple[Seed, str]] = []
    conn = getattr(item, "connector", None) or seed.scope
    if conn:
        refs.append((Seed("connector", conn), f"menu item {seed.scope}.{seed.name}"))
    target = getattr(item, "target", None)
    type_ = getattr(item, "type", None)
    if target and type_ in ("table", "form", "chart"):
        refs.append((Seed("screen", target, seed.scope), f"menu item {seed.scope}.{seed.name} target"))
    elif target and type_ == "dashboard":
        refs.append((Seed("dashboard", target), f"menu item {seed.scope}.{seed.name} target"))
    return dep, refs


def _resolve_dashboard(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    dashboards = getattr(state, "dashboards", None)
    if dashboards is None:
        return None, []
    # Dashboards are scoped: find which scope holds this id.
    dash_scope = None
    dash = None
    for scope_name, dash_map in (dashboards.dashboards or {}).items():
        if isinstance(dash_map, dict) and seed.name in dash_map:
            dash_scope = scope_name
            dash = dash_map[seed.name]
            break
    if dash is None:
        return None, []
    dep = Dependency(kind="dashboard", name=seed.name, scope=dash_scope, config=_dump(dash))
    refs: list[tuple[Seed, str]] = []
    dash_conn = getattr(dash, "connector", None)
    if dash_conn:
        refs.append((Seed("connector", dash_conn), f"dashboard {seed.name}"))
    for w in getattr(dash, "widgets", None) or []:
        w_conn = getattr(w, "connector", None) or dash_conn
        if w_conn:
            refs.append((Seed("connector", w_conn), f"dashboard {seed.name} widget"))
        w_q = getattr(w, "query", None)
        if w_q and w_conn:
            refs.append((Seed("query", w_q, w_conn), f"dashboard {seed.name} widget query"))
        w_chart = getattr(w, "chart", None)
        if w_chart and w_conn:
            refs.append((Seed("chart", w_chart, w_conn), f"dashboard {seed.name} widget chart-ref"))
    for filt in getattr(dash, "filters", None) or []:
        opts = getattr(filt, "options", None)
        if opts is not None:
            o_conn = getattr(opts, "connector", None)
            o_q = getattr(opts, "query", None)
            if o_conn and o_q:
                refs.append((Seed("query", o_q, o_conn), f"dashboard {seed.name} filter options"))
                refs.append((Seed("connector", o_conn), f"dashboard {seed.name} filter options"))
    return dep, refs


def _resolve_connector(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    cr = getattr(state, "connectors", None)
    if cr is None:
        return None, []
    if seed.name not in cr.names():
        return None, []
    conn = cr.get(seed.name)
    cfg = getattr(conn, "config", None)
    if cfg is None:
        return None, []
    dep = Dependency(kind="connector", name=seed.name, scope=None, config=_dump(cfg))
    refs: list[tuple[Seed, str]] = []
    pool = getattr(cfg, "pool", None)
    if pool:
        refs.append((Seed("pool", pool), f"connector {seed.name}"))
    return dep, refs


def _resolve_query(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    cr = getattr(state, "connectors", None)
    if cr is None or not seed.scope or seed.scope not in cr.names():
        return None, []
    conn = cr.get(seed.scope)
    cfg = getattr(conn, "config", None)
    if cfg is None:
        return None, []
    q = next((q for q in (getattr(cfg, "queries", None) or []) if getattr(q, "name", None) == seed.name), None)
    if q is None:
        return None, []
    dep = Dependency(kind="query", name=seed.name, scope=seed.scope, config=_dump(q))
    refs: list[tuple[Seed, str]] = []
    # Column hints on this query reference DD entries.
    for col in getattr(q, "columns", None) or []:
        dd_seed = _resolve_dd_via_column_name(state, getattr(col, "dd", None) or getattr(col, "name", ""), connector=seed.scope)
        if dd_seed:
            refs.append((dd_seed, f"query {seed.scope}.{seed.name} column {col.name}"))
    return dep, refs


def _resolve_api_endpoint(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    cr = getattr(state, "connectors", None)
    if cr is None or not seed.scope or seed.scope not in cr.names():
        return None, []
    conn = cr.get(seed.scope)
    cfg = getattr(conn, "config", None)
    ep = next((e for e in (getattr(cfg, "endpoints", None) or []) if getattr(e, "name", None) == seed.name), None)
    if ep is None:
        return None, []
    dep = Dependency(kind="api_endpoint", name=seed.name, scope=seed.scope, config=_dump(ep))
    # The endpoint's connector must come too (the api_call action already enqueues it but for
    # standalone api_endpoint seeds we add it ourselves).
    return dep, [(Seed("connector", seed.scope), f"api_endpoint {seed.scope}.{seed.name}")]


def _resolve_pool(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    cr = getattr(state, "connectors", None)
    pools = getattr(cr, "pools", None) if cr is not None else None
    if pools is None:
        return None, []
    # PoolRegistry has both the raw configs and the materialised engines. The config carries
    # the connection details we want to dump.
    pool_cfg = (getattr(pools, "_configs", None) or {}).get(seed.name)
    if pool_cfg is None:
        return None, []
    return Dependency(kind="pool", name=seed.name, scope=None, config=_dump(pool_cfg)), []


def _resolve_dictionary_entry(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    d = _get_dictionary(state)
    if d is None:
        return None, []
    scope = seed.scope or "shared"
    if scope == "shared":
        section_entries = d.entries
    else:
        section = (d.connectors or {}).get(scope)
        section_entries = section.entries if section is not None else {}
    entry = (section_entries or {}).get(seed.name)
    if entry is None:
        return None, []
    dep = Dependency(kind="dictionary_entry", name=seed.name, scope=scope, config=_dump(entry))
    refs: list[tuple[Seed, str]] = []
    rules = (getattr(entry, "rules", None) or "").upper()
    rv = getattr(entry, "rules_values", None)
    if rules == "LOOKUP" and rv:
        refs.append((Seed("lookup", rv, scope), f"entry {scope}.{seed.name} rules=LOOKUP"))
    elif rules in ("SEQUENCE", "NN") and rv:
        refs.append((Seed("sequence", rv, scope), f"entry {scope}.{seed.name} rules={rules}"))
    elif rules == "ENUM" and rv:
        refs.append((Seed("enum", rv, scope), f"entry {scope}.{seed.name} rules=ENUM"))
    return dep, refs


def _resolve_dict_target(state: Any, seed: Seed, coll: str) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    d = _get_dictionary(state)
    if d is None:
        return None, []
    scope = seed.scope or "shared"
    if scope == "shared":
        m = getattr(d, coll, None)
    else:
        section = (d.connectors or {}).get(scope)
        m = getattr(section, coll, None) if section is not None else None
    target = (m or {}).get(seed.name)
    if target is None:
        return None, []
    dep = Dependency(kind=seed.kind, name=seed.name, scope=scope, config=_dump(target))
    refs: list[tuple[Seed, str]] = []
    # Lookup / Sequence may pull in their backing query.
    q = getattr(target, "query", None)
    q_conn = getattr(target, "connector", None) or (scope if scope != "shared" else None)
    if q and q_conn:
        refs.append((Seed("query", q, q_conn), f"{seed.kind} {scope}.{seed.name} query"))
        refs.append((Seed("connector", q_conn), f"{seed.kind} {scope}.{seed.name} connector"))
    # Sequence's previous_query.
    pq = getattr(target, "previous_query", None)
    if pq and q_conn:
        refs.append((Seed("query", pq, q_conn), f"{seed.kind} {scope}.{seed.name} previous_query"))
    return dep, refs


def _resolve_chart(state: Any, seed: Seed) -> tuple[Dependency | None, list[tuple[Seed, str]]]:
    charts = getattr(state, "charts", None)
    if charts is None or not seed.scope:
        return None, []
    chart = (charts.charts or {}).get(seed.scope, {}).get(seed.name)
    if chart is None:
        return None, []
    dep = Dependency(kind="chart", name=seed.name, scope=seed.scope, config=_dump(chart))
    refs: list[tuple[Seed, str]] = []
    c_conn = getattr(chart, "connector", None)
    c_q = getattr(chart, "query", None)
    if c_conn:
        refs.append((Seed("connector", c_conn), f"chart {seed.scope}.{seed.name}"))
    if c_q and c_conn:
        refs.append((Seed("query", c_q, c_conn), f"chart {seed.scope}.{seed.name} query"))
    return dep, refs
