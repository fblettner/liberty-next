"""Move a query / table / sequence / lookup from one connector to another — the cross-file
companion to :mod:`liberty.web.rename`.

``rename`` changes an entity's *name* and rewrites every reference to keep the name in sync.
``move`` keeps the name but changes which connector *owns* the definition, and rewrites every
reference's ``connector`` so consumers follow it. The canonical case: a table/lookup that was
replicated into a second connector (v1 did this for performance) is moved back to read from the
source connector directly — without hand-duplicating the SQL and hunting every reference.

What it does, in one atomic pass (nothing written unless every rewritten doc re-parses):

1. **connectors.toml** — relocate the ``[[connectors.<from>.<coll>]]`` entry to
   ``[[connectors.<to>.<coll>]]`` (``coll`` = tables / queries / sequences / lookups).
2. **References** — for every consumer that points at ``(<from>, <name>)``, repoint its
   ``connector`` to ``<to>``. A reference's effective connector is its own ``connector`` field
   when set, else inherited (screen's connector / chart-dashboard scope / menu app).

The safety rule for nodes that share **one** connector across **several** query fields (screens'
read/update/insert/delete, nested tabs, sequences' query + previous_query): flip that node's
connector only when *every* query field that resolves to ``<from>`` is part of this move — i.e.
the node has nothing left behind in ``<from>``. When it does, the flip would silently redirect
those siblings, so the move leaves the node alone and *reports* it for manual fixing instead
(the "smart flip + report" behaviour). Self-describing single-reference consumers (lookups,
menus, charts, dashboard widgets, actions' run_query) always flip safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from liberty.charts.config import parse_charts
from liberty.connectors.config import parse_connectors
from liberty.connectors.dictionary import parse_dictionary
from liberty.dashboards.config import parse_dashboards
from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens
from liberty.web.rename import RenameError, _dump_doc, _load_doc, _validate

# Shared-actions live in their own file with their own parser; import lazily in the orchestrator
# to avoid a hard dependency when an actions.toml path isn't supplied.

# kind → the connectors.toml array-of-tables section that holds the definition.
_COLL_BY_KIND = {"table": "tables", "query": "queries", "sequence": "sequences", "lookup": "lookups"}
_CRUD = ("get", "put", "post", "delete")
# Every field whose value is a query NAME (resolved against the node's effective connector). A
# menu leaf's ``target`` is query-valued only when ``type == "query"`` — handled separately.
_QUERY_REF_FIELDS = ("read_query", "update_query", "insert_query", "delete_query", "query", "previous_query")


class MoveError(RenameError):
    """Raised when a move can't proceed (definition not found, target missing, name collision)."""


@dataclass
class ManualRef:
    """A reference the move could NOT safely rewrite — the operator must fix it by hand. Carries
    the same ``deep_link`` shape as a :class:`~liberty.web.usages.Usage` so the UI can route to it."""

    where: str
    reason: str
    deep_link: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"where": self.where, "reason": self.reason, "deep_link": self.deep_link}


@dataclass
class MoveResult:
    """What :func:`move_query` changed. ``files`` maps each touched config path to a count of
    rewritten references; ``manual_refs`` lists references left for the operator (screens whose
    connector is shared with queries staying behind). The route returns this verbatim."""

    kind: str
    name: str
    from_connector: str
    to_connector: str
    files: dict[str, int] = field(default_factory=dict)
    manual_refs: list[ManualRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def total_refs(self) -> int:
        return sum(self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "from_connector": self.from_connector,
            "to_connector": self.to_connector,
            "files": self.files,
            "total_refs": self.total_refs(),
            "manual_refs": [m.to_dict() for m in self.manual_refs],
            "warnings": self.warnings,
        }


# ── connectors.toml — relocate the definition ───────────────────────────────────────────────


def _relocate_definition(doc: Any, *, kind: str, name: str, from_conn: str, to_conn: str) -> set[str]:
    """Move the ``[[connectors.<from>.<coll>]]`` entry named *name* to ``[[connectors.<to>.<coll>]]``.

    Returns the set of **query names** the move affects — ``{name}`` for query / sequence / lookup,
    or the synthesised ``<name>_<crud>`` slot names for a table — which the reference pass matches
    against. Raises :class:`MoveError` on missing source, missing target, or a name collision."""
    conns = doc.get("connectors")
    if not isinstance(conns, dict) or from_conn not in conns:
        raise MoveError(f"connector {from_conn!r} not found in connectors.toml")
    if to_conn not in conns:
        raise MoveError(f"target connector {to_conn!r} does not exist — create it first (Settings → Connectors)")
    coll = _COLL_BY_KIND[kind]
    src = conns[from_conn].get(coll)
    if not isinstance(src, list):
        raise MoveError(f"{kind} {name!r} not found in connector {from_conn!r}")
    idx = next((i for i, e in enumerate(src) if isinstance(e, dict) and e.get("name") == name), None)
    if idx is None:
        raise MoveError(f"{kind} {name!r} not found in connector {from_conn!r}")
    dst = conns[to_conn].get(coll)
    if isinstance(dst, list) and any(isinstance(e, dict) and e.get("name") == name for e in dst):
        raise MoveError(f"{kind} {name!r} already exists in connector {to_conn!r} — delete it there first, or rename one")

    entry = src[idx]
    # Compute affected query names BEFORE we drop tomlkit wrappers (need the slot keys for a table).
    if kind == "table":
        affected = {f"{name}_{c}" for c in _CRUD if entry.get(c) is not None}
        if not affected:  # a table with no CRUD slots is odd, but be permissive
            affected = {f"{name}_{c}" for c in _CRUD}
    else:
        affected = {name}

    # ``unwrap()`` turns the tomlkit table into a plain dict so it can be re-homed under the target
    # without tomlkit's parent-pointer bookkeeping complaining (the entry carries no comments — the
    # SQL is a string value — so nothing meaningful is lost).
    plain = entry.unwrap() if hasattr(entry, "unwrap") else dict(entry)
    del src[idx]
    if dst is None:
        conns[to_conn][coll] = tomlkit.aot()
        dst = conns[to_conn][coll]
    dst.append(plain)
    return affected


# ── reference rewrite — self-describing + context-inheriting nodes ───────────────────────────


def _node_refs(node: dict[str, Any]) -> list[str]:
    """Every query-NAME value on this node (the ref fields + a ``type=="query"`` menu leaf's
    ``target``). These all resolve against the node's single effective connector, which is what
    makes the all-or-nothing flip rule correct."""
    out = [node[f] for f in _QUERY_REF_FIELDS if isinstance(node.get(f), str) and node[f]]
    if node.get("type") == "query" and isinstance(node.get("target"), str) and node["target"]:
        out.append(node["target"])
    return out


def _flip_node(node: dict[str, Any], *, to_conn: str) -> None:
    """Set this node's ``connector`` to *to_conn* (covers every query ref the node carries — the
    caller has already checked they all belong to the move)."""
    node["connector"] = to_conn


def _move_refs(
    node: Any, *, from_conn: str, to_conn: str, affected: set[str], default_conn: str | None,
    manual: list[ManualRef], where: str, deep_link: dict[str, Any],
) -> int:
    """Generic context-threading walk for the *non-screen* files (dictionary / menus / charts /
    dashboards / actions). Threads the effective connector (own ``connector`` field, else
    inherited *default_conn*) and, for any node that resolves to ``from_conn`` and references at
    least one affected query, applies the all-or-nothing rule: flip the node's ``connector`` to
    *to_conn* when *every* query ref on the node is affected, else record a ManualRef.

    Returns the number of nodes flipped."""
    n = 0
    if isinstance(node, dict):
        own = node.get("connector")
        ctx = own if isinstance(own, str) and own else default_conn
        if ctx == from_conn:
            refs = _node_refs(node)
            matched = [r for r in refs if r in affected]
            if matched:
                if all(r in affected for r in refs):
                    _flip_node(node, to_conn=to_conn)
                    n += 1
                else:
                    leftover = sorted(set(refs) - affected)
                    manual.append(ManualRef(
                        where=where,
                        reason=f"shares connector {from_conn!r} with queries staying behind ({', '.join(leftover)})",
                        deep_link=deep_link,
                    ))
        for v in node.values():
            n += _move_refs(v, from_conn=from_conn, to_conn=to_conn, affected=affected,
                            default_conn=ctx, manual=manual, where=where, deep_link=deep_link)
    elif isinstance(node, list):
        for v in node:
            n += _move_refs(v, from_conn=from_conn, to_conn=to_conn, affected=affected,
                            default_conn=default_conn, manual=manual, where=where, deep_link=deep_link)
    return n


def _move_refs_by_scope(scoped: Any, *, from_conn: str, to_conn: str, affected: set[str],
                        manual: list[ManualRef], kind_label: str) -> int:
    """Scope-keyed files (``[charts.<scope>.<id>]`` / ``[dashboards.<scope>.<id>]``): walk each
    scope's subtree with the scope as the default connector context (charts inherit it; a widget's
    explicit ``connector`` still overrides)."""
    n = 0
    if isinstance(scoped, dict):
        for scope, by_id in scoped.items():
            n += _move_refs(
                by_id, from_conn=from_conn, to_conn=to_conn, affected=affected,
                default_conn=scope if isinstance(scope, str) else None,
                manual=manual, where=f"{kind_label} {scope}", deep_link={},
            )
    return n


# ── screens — smart flip + report ───────────────────────────────────────────────────────────


def _collect_inheriting_from_refs(node: Any, *, from_conn: str, default_conn: str | None) -> list[str]:
    """Every query ref under *node* that resolves to ``from_conn`` **by inheritance** (i.e. on a
    node with no explicit ``connector`` of its own). Used to decide whether flipping a screen's
    connector is safe: if any inheriting ref points at a query NOT in the move, flipping would
    break it. Nodes that carry their own ``connector`` are handled independently and excluded."""
    out: list[str] = []
    if isinstance(node, dict):
        own = node.get("connector")
        if isinstance(own, str) and own:
            ctx: str | None = own
            inheriting = False
        else:
            ctx = default_conn
            inheriting = True
        if inheriting and ctx == from_conn:
            out.extend(_node_refs(node))
        for v in node.values():
            out.extend(_collect_inheriting_from_refs(v, from_conn=from_conn, default_conn=ctx))
    elif isinstance(node, list):
        for v in node:
            out.extend(_collect_inheriting_from_refs(v, from_conn=from_conn, default_conn=default_conn))
    return out


def _set_explicit_on_own_connector_nodes(node: Any, *, from_conn: str, to_conn: str,
                                         affected: set[str], manual: list[ManualRef], where: str,
                                         deep_link: dict[str, Any]) -> int:
    """Within a screen subtree, repoint nodes that carry their OWN ``connector == from_conn`` (a
    nested tab or an action with an explicit connector) — independent of the screen's own flip.
    Same all-or-nothing rule. Inheriting nodes are NOT touched here (the screen-level flip owns
    them). Returns the count flipped."""
    n = 0
    if isinstance(node, dict):
        own = node.get("connector")
        if isinstance(own, str) and own == from_conn:
            refs = _node_refs(node)
            matched = [r for r in refs if r in affected]
            if matched:
                if all(r in affected for r in refs):
                    _flip_node(node, to_conn=to_conn)
                    n += 1
                else:
                    leftover = sorted(set(refs) - affected)
                    manual.append(ManualRef(
                        where=where, reason=f"explicit connector {from_conn!r} shared with queries staying behind ({', '.join(leftover)})",
                        deep_link=deep_link))
        for v in node.values():
            n += _set_explicit_on_own_connector_nodes(v, from_conn=from_conn, to_conn=to_conn,
                                                      affected=affected, manual=manual, where=where, deep_link=deep_link)
    elif isinstance(node, list):
        for v in node:
            n += _set_explicit_on_own_connector_nodes(v, from_conn=from_conn, to_conn=to_conn,
                                                      affected=affected, manual=manual, where=where, deep_link=deep_link)
    return n


def _move_screen_refs(screens: Any, *, from_conn: str, to_conn: str, affected: set[str],
                      manual: list[ManualRef]) -> int:
    """Rewrite screen references. ``screens`` is the ``{app: {sid: screen}}`` map. A screen's
    effective connector is ``screen.connector`` or the app key. For each screen on ``from_conn``:

    * repoint nested tabs / actions that carry their OWN ``connector == from`` (all-or-nothing);
    * then decide the screen's own connector: flip to ``to`` only when every ref that resolves to
      ``from`` *by inheritance* (top-level slots + inheriting children) is in the move; otherwise
      leave the screen and report it (its read/update/insert/delete still point into ``from``)."""
    n = 0
    if not isinstance(screens, dict):
        return 0
    for app, smap in screens.items():
        if not isinstance(smap, dict):
            continue
        for sid, screen in smap.items():
            if not isinstance(screen, dict):
                continue
            screen_conn = screen.get("connector") or app
            link = {"editor": "screens", "app": app, "screen": sid}
            where = f"{app}.{sid}"
            # 1) explicit-connector sub-entities flip independently.
            n += _set_explicit_on_own_connector_nodes(
                screen, from_conn=from_conn, to_conn=to_conn, affected=affected, manual=manual,
                where=where, deep_link=link,
            )
            if screen_conn != from_conn:
                continue
            # 2) the screen's own (inherited) refs — top-level slots + any inheriting descendants.
            inherited_refs = _collect_inheriting_from_refs(screen, from_conn=from_conn, default_conn=app)
            matched = [r for r in inherited_refs if r in affected]
            if not matched:
                continue
            if all(r in affected for r in inherited_refs):
                screen["connector"] = to_conn
                n += 1
            else:
                leftover = sorted(set(inherited_refs) - affected)
                manual.append(ManualRef(
                    where=where,
                    reason=(f"screen connector {from_conn!r} is shared with queries staying behind "
                            f"({', '.join(leftover)}) — set a connector on the affected slot/tab or move those too"),
                    deep_link=link,
                ))
    return n


# ── orchestrator ────────────────────────────────────────────────────────────────────────────


def move_query(
    kind: str,
    name: str,
    from_connector: str,
    to_connector: str,
    *,
    connectors_path: Path,
    screens_path: Path,
    menus_path: Path,
    dictionary_path: Path,
    charts_path: Path | None = None,
    dashboards_path: Path | None = None,
    actions_path: Path | None = None,
) -> MoveResult:
    """Move *name* (a *kind* ∈ table / query / sequence / lookup) from *from_connector* to
    *to_connector*, relocating the definition in connectors.toml and rewriting every safe
    reference. Validates every touched doc before writing; on any failure nothing is written.

    Does NOT reload — the caller runs ``POST /admin/reload`` afterwards."""
    if kind not in _COLL_BY_KIND:
        raise MoveError(f"move kind {kind!r} not supported — one of: {', '.join(sorted(_COLL_BY_KIND))}")
    if from_connector == to_connector:
        raise MoveError("source and target connectors are the same — nothing to move")

    result = MoveResult(kind=kind, name=name, from_connector=from_connector, to_connector=to_connector)

    # Load every doc up front (missing file → empty doc, recorded as 0 touches).
    docs: dict[str, tuple[Path, Any]] = {}
    for label, path in (
        ("connectors", connectors_path), ("screens", screens_path), ("menus", menus_path),
        ("dictionary", dictionary_path), ("charts", charts_path), ("dashboards", dashboards_path),
        ("actions", actions_path),
    ):
        if path is None:
            continue
        docs[label] = (path, _load_doc(label, path))
        if not (path.exists() and path.read_text(encoding="utf-8").strip()):
            result.files[str(path)] = 0

    # 1) relocate the definition (raises on not-found / collision / missing target).
    conn_path, conn_doc = docs["connectors"]
    affected = _relocate_definition(conn_doc, kind=kind, name=name, from_conn=from_connector, to_conn=to_connector)
    result.files[str(conn_path)] = 1

    # 2) references.
    scr_path, scr_doc = docs["screens"]
    result.files[str(scr_path)] = _move_screen_refs(
        scr_doc.get("screens"), from_conn=from_connector, to_conn=to_connector,
        affected=affected, manual=result.manual_refs,
    )

    menu_path, menu_doc = docs["menus"]
    n_menu = 0
    menus = menu_doc.get("menus")
    if isinstance(menus, dict):
        for app, acfg in menus.items():
            n_menu += _move_refs(acfg, from_conn=from_connector, to_conn=to_connector, affected=affected,
                                 default_conn=app, manual=result.manual_refs, where=f"menu {app}",
                                 deep_link={"editor": "menus", "app": app})
    result.files[str(menu_path)] = n_menu

    dict_path, dict_doc = docs["dictionary"]
    n_dict = 0
    for sect in ("lookups", "sequences"):
        n_dict += _move_refs(dict_doc.get(sect), from_conn=from_connector, to_conn=to_connector,
                             affected=affected, default_conn=None, manual=result.manual_refs,
                             where=f"dictionary {sect}", deep_link={"editor": "dictionary"})
    dconns = dict_doc.get("connectors")
    if isinstance(dconns, dict):
        for scope, scfg in dconns.items():
            n_dict += _move_refs(scfg, from_conn=from_connector, to_conn=to_connector, affected=affected,
                                 default_conn=scope, manual=result.manual_refs,
                                 where=f"dictionary {scope}", deep_link={"editor": "dictionary"})
    result.files[str(dict_path)] = n_dict

    if "charts" in docs:
        ch_path, ch_doc = docs["charts"]
        result.files[str(ch_path)] = _move_refs_by_scope(
            ch_doc.get("charts"), from_conn=from_connector, to_conn=to_connector,
            affected=affected, manual=result.manual_refs, kind_label="chart scope")
    if "dashboards" in docs:
        da_path, da_doc = docs["dashboards"]
        result.files[str(da_path)] = _move_refs_by_scope(
            da_doc.get("dashboards"), from_conn=from_connector, to_conn=to_connector,
            affected=affected, manual=result.manual_refs, kind_label="dashboard scope")

    if "actions" in docs:
        act_path, act_doc = docs["actions"]
        # Shared actions have no inherited connector — a run_query step must carry its own.
        result.files[str(act_path)] = _move_refs(
            act_doc.get("actions"), from_conn=from_connector, to_conn=to_connector, affected=affected,
            default_conn=None, manual=result.manual_refs, where="shared action",
            deep_link={"editor": "actions"})

    # 3) validate every touched doc, then write the batch (nothing written if any fails to parse).
    _validate("connectors", conn_doc, parse_connectors, conn_path)
    for label, parser in (
        ("screens", parse_screens), ("menus", parse_menus), ("dictionary", parse_dictionary),
        ("charts", parse_charts), ("dashboards", parse_dashboards),
    ):
        if label in docs and result.files.get(str(docs[label][0])):
            _validate(label, docs[label][1], parser, docs[label][0])
    if "actions" in docs and result.files.get(str(docs["actions"][0])):
        from liberty.actions.config import parse_actions
        _validate("actions", docs["actions"][1], parse_actions, docs["actions"][0])

    for label, (path, doc) in docs.items():
        if not result.files.get(str(path), 0):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump_doc(label, doc), encoding="utf-8")

    return result
