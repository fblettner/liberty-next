"""Apply a module (catalogue v2, :mod:`liberty.modules`): preview, then write its configuration.

:func:`build_plan` is pure — the module, each table's live columns, what the configuration already
holds — and returns what applying would do: per screen its read query (``<id>_get``: the module's
columns of the table, physical names, ordered by the key), the screen (columns by data item, the
dialog's form tabs and the nested tables of its details — filtered on the link columns, hidden), the
menu folder and items, and what already exists (kept as it is — applying never overwrites). The JDE
prefix of each table is the one that makes every key item a column (``AB`` + ``AN8``), unless the
module forces it. A table or a key item that cannot be found is an error: nothing is written.

:func:`write_plan` writes the new entries additively into the raw files — connectors.toml through
tomlkit (comments kept), screens / menus through tomllib + tomli_w (the big files, see
``liberty.web.rename``) — after validating the result; the caller then creates the dictionary
entries and reloads. Display first: the screens are read-only, no write query is generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from liberty.modules import Module, ModuleScreen

_TOKEN = re.compile(r"^#SCHEMA\.([A-Z0-9_]+)#$")


def _from(schema: str | None, table: str) -> str:
    """``#SCHEMA.DTA#.F0101#DBLINK.DTA#`` for a schema token (the JDE convention of the shipped
    queries), ``SCHEMA.TABLE`` for a plain schema, the bare table without one."""
    if not schema:
        return table
    m = _TOKEN.match(schema)
    return f"{schema}.{table}#DBLINK.{m.group(1)}#" if m else f"{schema}.{table}"


def _prefix(forced: str | None, key: list[str], items: list[str], columns: list[str]) -> str | None:
    """The column prefix under which every *key* item is a column of *columns* (the one matching the
    most *items* wins a tie); *forced* when given; ``""`` for a table without prefix."""
    if forced is not None:
        return forced
    cols = {c.upper() for c in columns}
    first = key[0]
    candidates = {c[: len(c) - len(first)] for c in cols if c.endswith(first) and len(c) - len(first) <= 4}
    ok = [p for p in candidates if all(p + k in cols for k in key)]
    if not ok:
        return None
    return max(sorted(ok), key=lambda p: sum(p + i in cols for i in items))


def resolve_prefix(screen: ModuleScreen, columns: list[str]) -> str | None:
    """The column prefix of *screen*'s table: forced, else the one under which every key item is a
    column (the most items matched wins a tie); ``""`` for a table without prefix."""
    return _prefix(screen.prefix, screen.key, screen.items(), columns)


@dataclass
class ScreenPlan:
    id: str
    table: str
    prefix: str | None
    status: str                      # new / exists
    query: str
    query_status: str                # new / exists
    columns: int = 0
    missing: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ModulePlan:
    module: str
    connector: str
    app: str
    screens: list[ScreenPlan]
    menu_folder: str                 # new / exists
    menu_items: list[dict[str, str]]  # {id, status}
    errors: list[str]
    replace: bool = False            # the module's existing entries rewritten (else kept)
    # what applying writes (the new entries — and, with replace, the module's existing ones)
    tables: list[dict[str, Any]] = field(default_factory=list)
    screen_defs: dict[str, dict[str, Any]] = field(default_factory=dict)
    menu_defs: list[dict[str, Any]] = field(default_factory=list)
    dictionary_sources: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "module": self.module, "connector": self.connector, "app": self.app,
            "screens": [s.__dict__ for s in self.screens],
            "menu": {"folder": self.menu_folder, "items": self.menu_items},
            "errors": self.errors, "replace": self.replace,
            "new": {"queries": [t["name"] + "_get" for t in self.tables], "screens": sorted(self.screen_defs),
                    "menu_items": [m["id"] for m in self.menu_defs]},
        }


def build_plan(
    module: Module,
    live: dict[str, list[str] | None],
    *,
    existing_tables: set[str],
    existing_screens: set[str],
    existing_menu_ids: set[str],
    replace: bool = False,
) -> ModulePlan:
    """What applying *module* does. *live* maps a table name (upper case) to its columns (None: not
    found); the ``existing_*`` sets name what the configuration holds already (the connector's
    tables, the app's screens, the app's menu item ids). With *replace* the module's own entries that
    exist are rewritten (``replace``) instead of kept (``exists``)."""
    errors: list[str] = []
    plans: list[ScreenPlan] = []
    # screen id → {data item → (output column, select expression)}
    physical: dict[str, dict[str, tuple[str, str]]] = {}
    joins_sql: dict[str, list[tuple[str, str, str]]] = {}    # screen id → [(table, alias, ON …)]
    held = "replace" if replace else "exists"
    for s in module.screens:
        p = ScreenPlan(id=s.id, table=s.table, prefix=None, query=f"{s.id}_get",
                       status=held if s.id in existing_screens else "new",
                       query_status=held if s.id.lower() in existing_tables else "new")
        plans.append(p)
        cols = live.get(s.table.upper())
        if cols is None:
            p.error = f"table {s.table} not found"
            errors.append(f"{s.id}: {p.error}")
            continue
        prefix = resolve_prefix(s, cols)
        if prefix is None:
            p.error = f"no prefix of {s.table} has every key column {s.key}"
            errors.append(f"{s.id}: {p.error}")
            continue
        p.prefix = prefix
        upper = {c.upper() for c in cols}
        qual = "T0." if s.joins else ""     # the table's columns qualified once a lookup is joined
        found: dict[str, tuple[str, str]] = {}
        # the joined lookups first resolved, the table's own columns win a shared item
        joined: dict[str, tuple[str, str]] = {}
        joins_sql[s.id] = []
        for n, j in enumerate(s.joins, 1):
            jcols = live.get(j.table.upper())
            jp = _prefix(j.prefix, j.on, j.columns, jcols) if jcols is not None else None
            if jcols is None or jp is None:
                p.error = f"joined table {j.table} not found" if jcols is None else f"no prefix of {j.table} has the join columns {j.on}"
                errors.append(f"{s.id}: {p.error}")
                break
            absent = [i for i in j.on if prefix + i not in upper]
            if absent:
                p.error = f"join on {absent}: not columns of {s.table}"
                errors.append(f"{s.id}: {p.error}")
                break
            ju = {c.upper() for c in jcols}
            alias = f"T{n}"
            on = " AND ".join(f"T0.{prefix}{i} = {alias}.{jp}{i}" for i in j.on)
            joins_sql[s.id].append((_from(j.schema_ or module.screen_schema(s), j.table), alias, on))
            for c in j.columns:
                if jp + c in ju:
                    joined.setdefault(c, (jp + c, f"{alias}.{jp}{c}"))
        if p.error:
            continue
        own = s.items() if s.columns else [*s.items(), *[c[len(prefix):] for c in cols if c.upper().startswith(prefix) and c[len(prefix):] not in s.items()]]
        for i in own:
            if prefix + i in upper:
                found[i] = (prefix + i, f"{qual}{prefix}{i}")
            elif i in joined:
                found[i] = joined[i]
        p.missing = [i for i in own if i not in found]
        if any(k in p.missing for k in s.key):
            p.error = f"key column missing: {[k for k in s.key if k in p.missing]}"
            errors.append(f"{s.id}: {p.error}")
            continue
        physical[s.id] = found
        p.columns = len(found)

    parent_links: dict[str, list[str]] = {}            # child id → its link items (filtered)
    for s in module.screens:
        for d in s.details:
            parent_links.setdefault(d.screen, []).extend(x.child for x in d.on)
    menu_ids = {x.id for x in module.menu_screens()}

    plan = ModulePlan(module=module.id, connector=module.connector, app=module.app, screens=plans,
                      menu_folder=(held if module.id in existing_menu_ids else "new"),
                      menu_items=[], errors=errors, replace=replace)
    if errors:
        return plan

    for s in module.screens:
        found = physical[s.id]
        name = {i: c for i, (c, _) in found.items()}
        schema = module.screen_schema(s)
        # the grid in the module's order (its ``columns``, the joined lookups not listed after them),
        # then the key columns it left out, then the dialog-only columns
        joined_items = [c for j in s.joins for c in j.columns if c in found]
        grid = [*[c for c in s.columns if c in found], *[c for c in joined_items if c not in s.columns]] if s.columns else list(found)
        order = [*grid, *[k for k in s.key if k not in grid], *[i for i in found if i not in grid and i not in s.key]]
        if replace or s.id.lower() not in existing_tables:
            key_cols = ", ".join(found[k][1] for k in s.key)
            select = ",\n    ".join(found[i][1] for i in order)
            src = _from(schema, s.table)
            if joins_sql[s.id]:
                src += " T0" + "".join(f"\nLEFT JOIN\n  {t} {a}\n  ON {on}" for t, a, on in joins_sql[s.id])
            plan.tables.append({
                "name": s.id, "label": f"{s.label} ({s.table})",
                "get": {"sql": f"SELECT\n    {select}\nFROM\n  {src}\nORDER BY\n  {key_cols}"},
            })
        plan.dictionary_sources.append({"connector": module.connector, "table": s.table, "schema": schema})
        if s.id in existing_screens and not replace:
            continue
        links = set(parent_links.get(s.id, ()))
        detail_only = s.id not in menu_ids
        columns = []
        for item in order:
            if item not in found:
                continue
            hint: dict[str, Any] = {"name": name[item], "dd": item}
            if item in s.key:
                hint["key"] = True
            if item in links or item in s.filters:
                hint["filter"] = True          # filtered in the database: the parent's binds, the filter panel
            if (item in links and detail_only) or item not in grid:
                hint["hidden"] = True          # a link is shown where the screen is a list of its own
            columns.append(hint)
        tabs: list[dict[str, Any]] = []
        if s.tabs:
            for i, t in enumerate(s.tabs):
                tab = {"id": f"tab{i + 1}", "label": t.label, "type": "form", "cols": t.cols,
                       "fields": [{"name": name[f]} for f in t.fields if f in name]}
                if t.l:
                    tab["l"] = dict(t.l)
                tabs.append(tab)
        else:
            tabs.append({"id": "general", "label": "General", "l": {"fr": "Général"}, "type": "form", "cols": 2,
                         "fields": [{"name": name[i]} for i in order if i in name and not (i in links and detail_only)]})
        for d in s.details:
            child = {i: c for i, (c, _) in physical[d.screen].items()}
            binds = [{"param": child[x.child], "source": name[x.parent]} for x in d.on]
            tab = ({"id": d.screen, "label": d.label, "type": "nested_form", "form_screen": d.screen, "hide_on_add": True, "param_binds": binds}
                   if d.cardinality == "one" else
                   {"id": d.screen, "label": d.label, "type": "nested_table", "screen": d.screen, "hide_on_add": True, "param_binds": binds})
            if d.l:
                tab["l"] = dict(d.l)
            tabs.append(tab)
        screen = {"label": s.label, "connector": module.connector, "read_query": f"{s.id}_get",
                  "auto_load": True, "read_only": True, "columns": columns,
                  "dialog": {"title": s.label, "tabs": tabs}}
        if s.description:
            screen["description"] = s.description
        plan.screen_defs[s.id] = screen

    # the menu: the folder, then an item per menu screen
    if replace or module.id not in existing_menu_ids:
        folder: dict[str, Any] = {"id": module.id, "label": module.menu.label}
        if module.menu.l:
            folder["l"] = dict(module.menu.l)
        if module.menu.icon:
            folder["icon"] = module.menu.icon
        if module.menu.parent:
            folder["parent"] = module.menu.parent
        plan.menu_defs.append(folder)
    for s in module.menu_screens():
        status = (held if s.id in existing_menu_ids else "new")
        plan.menu_items.append({"id": s.id, "status": status})
        if status != "exists":
            item: dict[str, Any] = {"id": s.id, "parent": module.id, "label": s.label, "type": "screen", "target": s.id}
            if s.l:
                item["l"] = dict(s.l)
            if s.icon:
                item["icon"] = s.icon
            plan.menu_defs.append(item)
    return plan


def write_plan(plan: ModulePlan, *, connectors_path: Any, screens_path: Any, menus_path: Any, snapshot: Any = None) -> None:
    """Add the plan's entries to the three files — validated first, every file or none. Entries
    the plan doesn't carry are left exactly as they are (with replace, the module's own are
    rewritten in place); *snapshot* (``path → None``) runs before each
    write (the config history)."""
    import tomllib
    from pathlib import Path

    from liberty.connectors.config import ConnectorsFile
    from liberty.config import substitute_env
    from liberty.menus import MenusFile
    from liberty.screens import parse_screens
    from liberty.web.rename import _dump_doc, _load_doc

    paths = {"connectors": Path(connectors_path), "screens": Path(screens_path), "menus": Path(menus_path)}
    docs = {label: _load_doc(label, p) for label, p in paths.items()}
    if plan.tables:
        conn = docs["connectors"]["connectors"][plan.connector]
        if "tables" not in conn:
            import tomlkit
            conn["tables"] = tomlkit.aot()
        for t in plan.tables:
            at = next((i for i, x in enumerate(conn["tables"]) if str(x.get("name", "")).lower() == t["name"].lower()), None)
            if at is None:
                conn["tables"].append(t)
            else:                       # replace: the module's own table, rewritten in place
                conn["tables"][at] = t
    if plan.screen_defs:
        docs["screens"].setdefault("screens", {}).setdefault(plan.app, {}).update(plan.screen_defs)
    if plan.menu_defs:
        app_menu = docs["menus"].setdefault("menus", {}).setdefault(plan.app, {"label": plan.app})
        items = app_menu.setdefault("items", [])
        for m in plan.menu_defs:
            at = next((i for i, x in enumerate(items) if x.get("id") == m["id"]), None)
            if at is None:
                items.append(m)
            else:                       # replace: rewritten where it stands
                items[at] = m
    texts = {label: _dump_doc(label, doc) for label, doc in docs.items()}
    # validate the files as they will be read back — nothing written when one fails
    ConnectorsFile.model_validate(substitute_env(tomllib.loads(texts["connectors"])))
    parse_screens(tomllib.loads(texts["screens"]))
    MenusFile.model_validate(tomllib.loads(texts["menus"]))
    for label, changed in (("connectors", bool(plan.tables)), ("screens", bool(plan.screen_defs)), ("menus", bool(plan.menu_defs))):
        if changed:
            if snapshot is not None:
                snapshot(paths[label])
            paths[label].write_text(texts[label], encoding="utf-8")
