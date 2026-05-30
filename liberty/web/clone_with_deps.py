"""Clone an entity (screen / chart / dashboard) plus a chosen subset of its referenced
queries, rewriting the cloned entity's refs to point at the new query names.

Use case: customer wants to fork ``nomasx1.security_users`` to add a custom column.
Vanilla clone duplicates only the screen — every CRUD button on the clone still wires
to the BASE ``security_users_get`` / ``_put`` / ``_post`` / ``_delete``, so editing the
clone's column hints touches the base screen indirectly + can't add a new column without
also editing the shared SQL. Cloning WITH queries gives the operator a self-contained
fork:

    clone_with_deps(seed=Seed("screen", "security_users", "nomasx1"),
                    new_name="security_users_custom", suffix="custom",
                    options={"clone_queries": True})
        →
            screen   nomasx1.security_users_custom   (refs rewritten)
            query    nomasx1.security_users_get_custom
            query    nomasx1.security_users_put_custom
            query    nomasx1.security_users_post_custom
            query    nomasx1.security_users_delete_custom

The clone is wired up + isolated; the operator edits freely without touching the base.

This module is the BACKEND walker — it doesn't write to disk. The caller (a route
handler in admin.py) feeds the resulting :class:`CloneResult` into the existing
apply-proposal path so the writes go through the same Pydantic-validated +
tomli-w / tomlkit serialisers every other PUT uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from liberty.web.dependencies import Dependency, Seed, collect_dependencies


CLONE_KINDS: set[str] = {"query"}
"""Which dependency kinds can opt into being cloned. For Phase 1 we ship queries only —
that's the high-value case (screen / chart / dashboard customisation). Pools and
connectors are too big to safely clone (they bring credentials + every other connector
they host); DD entries are usually shared on purpose. Extend this set as new use cases
appear, with care."""


@dataclass(slots=True)
class CloneItem:
    """One entity in the clone result — either the seed (renamed) or a referenced
    dep that opted into cloning. ``original_key`` is the source ``(kind, scope, name)``
    so the apply step can include it in the rename map for ref-rewriting."""

    kind: str
    scope: str | None
    new_name: str
    original_key: tuple[str, str | None, str]
    config: dict[str, Any]              # already ref-rewritten + ready to write

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "scope": self.scope, "new_name": self.new_name,
            "original": {"kind": self.original_key[0], "scope": self.original_key[1], "name": self.original_key[2]},
            "config": self.config,
        }


@dataclass(slots=True)
class CloneResult:
    """Everything ``clone_with_deps`` produced. Items are pre-rewritten — the caller
    just routes each to the matching apply-proposal handler."""

    items: list[CloneItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "warnings": list(self.warnings),
        }


# ── walker ───────────────────────────────────────────────────────────────────────────────


def clone_with_deps(
    state: Any,
    *,
    seed: Seed,
    new_name: str,
    suffix: str | None = None,
    options: dict[str, bool] | None = None,
) -> CloneResult:
    """Walk *seed*'s dependency closure, pick the ones to clone per *options*, generate
    new names (seed gets *new_name*; deps get ``<original>_<suffix>``), and rewrite the
    seed's refs so the result is a self-contained fork.

    ``options`` flags (default: every CLONE_KINDS kind is OFF — caller opts in):
        ``clone_queries``   — also duplicate every referenced query under a new name

    Returns a :class:`CloneResult` ready for the apply route to write."""
    opts = options or {}
    if suffix is None:
        # Derive a reasonable suffix from new_name — the bit that distinguishes the clone.
        # ``security_users_custom`` clone of ``security_users`` → suffix = "custom"
        if new_name.startswith(seed.name + "_"):
            suffix = new_name[len(seed.name) + 1:]
        else:
            suffix = new_name  # fallback — operator picked an unrelated name

    manifest = collect_dependencies(state, [seed])
    result = CloneResult()

    # Build the rename map: source (kind, scope, name) → new name. Seed always renamed;
    # each clonable dep that the operator opted in for gets the suffix injected.
    rename_map: dict[tuple[str, str | None, str], str] = {
        (seed.kind, seed.scope, seed.name): new_name,
    }
    if opts.get("clone_queries"):
        for d in manifest.deps:
            if d.kind == "query":
                rename_map[d.key()] = _suffix_query_name(d.name, suffix)

    # Walk the manifest, produce a CloneItem for each entity in the rename map. The seed
    # itself comes from manifest.deps[0] (it's always included). Order matters: the seed
    # last so its rewritten config sees the final query names.
    mark_override = bool(opts.get("mark_override"))
    seed_item: CloneItem | None = None
    for d in manifest.deps:
        if d.key() not in rename_map:
            continue
        # Pass d.scope as the "effective connector fallback" so a screen whose own
        # ``connector`` field is None (= use the app default) still resolves its query
        # refs to the matching rename_map entry (which is keyed by the actual connector
        # scope = the app name by convention).
        new_cfg = _rewrite_refs(d.kind, d.config, rename_map, fallback_connector=d.scope)
        is_seed = d.key() == (seed.kind, seed.scope, seed.name)
        # Apply ``mark_override = true`` to the SEED only (operator's customer fork —
        # protects from a vendor upgrade overwriting it). Cloned dependency queries are
        # NOT auto-flagged: they're scaffolded support for the fork, not customer-
        # authored content; flagging them would lock them out of future query-level
        # vendor improvements (rare but possible). Operator can flip the flag on the
        # cloned queries by hand if they want.
        if is_seed and mark_override:
            new_cfg["override"] = True
        item = CloneItem(
            kind=d.kind,
            scope=d.scope,
            new_name=rename_map[d.key()],
            original_key=d.key(),
            config=new_cfg,
        )
        if is_seed:
            seed_item = item
        else:
            result.items.append(item)

    if seed_item:
        # Seed appended last so apply order writes deps first → seed's refs resolve
        # on the target. (Not strictly required because writes are atomic per file,
        # but easier to reason about.)
        result.items.append(seed_item)
    else:
        result.warnings.append(
            f"seed {seed.kind} {seed.scope or '-'}.{seed.name} not found in registry — nothing to clone"
        )

    # Surface dangling refs that aren't covered by the rename map (the rewriter leaves
    # them pointing at the originals — usually fine, but worth flagging).
    if opts.get("clone_queries"):
        all_query_keys = {d.key() for d in manifest.deps if d.kind == "query"}
        cloned = {k for k in rename_map if k[0] == "query"}
        missed = all_query_keys - cloned
        if missed:
            result.warnings.append(
                f"{len(missed)} queries referenced by the seed weren't cloned "
                f"(the clone keeps pointing at the originals): "
                f"{', '.join(sorted(f'{s}.{n}' for _, s, n in missed))}"
            )
    return result


# ── per-kind ref rewriters ──────────────────────────────────────────────────────────────


def _rewrite_refs(
    kind: str,
    cfg: dict[str, Any],
    rename_map: dict[tuple[str, str | None, str], str],
    *,
    fallback_connector: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *cfg* with every reference that lands in *rename_map* replaced
    by the new name. Per-kind because each entity references queries / connectors /
    screens / DD entries via different fields.

    *fallback_connector* — the connector to use when the entity itself doesn't pin one.
    For Screens this is the app name (the runtime convention: a screen with
    ``connector = None`` uses the app's same-named connector); without this the rewrite
    misses query refs because the rename_map is keyed by the real connector scope."""
    if kind == "screen":
        return _rewrite_screen(cfg, rename_map, fallback_connector=fallback_connector)
    if kind == "chart":
        return _rewrite_chart(cfg, rename_map)
    if kind == "dashboard":
        return _rewrite_dashboard(cfg, rename_map)
    if kind == "query":
        # Queries don't reference other queries (their SQL string references TABLES not
        # other query names). Connector + column hints could in theory but in practice
        # they don't transit through the clone path. Just return a copy.
        return _deep_copy(cfg)
    return _deep_copy(cfg)


def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    """JSON round-trip deep-copy — TOML data has no Dates / functions / refs so this is
    safe + cheap."""
    import json
    return json.loads(json.dumps(d, default=str))


_CRUD_SUFFIXES = ("_get", "_put", "_post", "_delete")


def _suffix_query_name(name: str, suffix: str) -> str:
    """Insert *suffix* into a query name in a way that preserves grouping.

    For CRUD-suffixed names (``<base>_get`` / ``_put`` / ``_post`` / ``_delete``), the
    suffix lands BEFORE the verb so the connector editor's tables-view classifier still
    sees ``<base>_<suffix>`` as the table base name:

        ``security_users_get``    + ``copy``   →  ``security_users_copy_get``
        ``security_users_put``    + ``copy``   →  ``security_users_copy_put``
        ``security_users_prop_put`` + ``copy`` →  ``security_users_prop_copy_put``

    For custom queries (no CRUD verb), the suffix is appended as before:

        ``get_active_orders``     + ``copy``   →  ``get_active_orders_copy``

    Without this, ``security_users_get_copy`` ends with ``_copy`` (not a CRUD verb),
    classifies as "custom" instead of "table", and disappears from the Tables view —
    even though the cloned screen still wires to it correctly."""
    for crud in _CRUD_SUFFIXES:
        if name.endswith(crud):
            base = name[: -len(crud)]
            return f"{base}_{suffix}{crud}"
    return f"{name}_{suffix}"


def _query_rename(
    rename_map: dict[tuple[str, str | None, str], str],
    connector: str | None,
    query_name: str | None,
) -> str | None:
    """Look up a query rename. Returns the new name, or ``query_name`` unchanged when
    not in the map."""
    if query_name is None:
        return None
    new = rename_map.get(("query", connector, query_name))
    return new or query_name


def _rewrite_screen(
    cfg: dict[str, Any],
    rename_map: dict[tuple[str, str | None, str], str],
    *,
    fallback_connector: str | None = None,
) -> dict[str, Any]:
    """Rewrite a Screen's query references — read/update/insert/delete, nested-form tabs,
    actions (run_query.query / navigate.to), export sheets, chart_id (treated as a
    chart rename, not yet supported)."""
    out = _deep_copy(cfg)
    # Effective connector: explicit ``Screen.connector`` wins, else the app default
    # (= the same-named connector — the runtime convention; see Screen.connector docs).
    eff_conn = out.get("connector") or fallback_connector
    for attr in ("read_query", "update_query", "insert_query", "delete_query"):
        if attr in out and out[attr]:
            out[attr] = _query_rename(rename_map, eff_conn, out[attr])
    dialog = out.get("dialog")
    if isinstance(dialog, dict):
        for tab in dialog.get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            tab_conn = tab.get("connector") or eff_conn
            for attr in ("read_query", "update_query", "insert_query"):
                if attr in tab and tab[attr]:
                    tab[attr] = _query_rename(rename_map, tab_conn, tab[attr])
            for action in _iter_actions(tab.get("actions")):
                _rewrite_action(action, rename_map, default_conn=eff_conn)
        for hook in ("on_load", "on_save", "on_cancel"):
            for action in _iter_actions(dialog.get(hook)):
                _rewrite_action(action, rename_map, default_conn=eff_conn)
    for hook in ("actions", "on_insert", "on_update", "on_delete", "row_menu"):
        for action in _iter_actions(out.get(hook)):
            _rewrite_action(action, rename_map, default_conn=eff_conn)
    export = out.get("export")
    if isinstance(export, dict):
        for sheet in export.get("sheets") or []:
            if isinstance(sheet, dict):
                s_conn = sheet.get("connector") or eff_conn
                if "query" in sheet and sheet["query"]:
                    sheet["query"] = _query_rename(rename_map, s_conn, sheet["query"])
    return out


def _iter_actions(actions: Any):
    """Walk an action list recursively (chains, IF / LOOP branches, on_success / on_error)."""
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        yield action
        for child_attr in ("steps", "if_true", "if_false", "body", "on_success", "on_error"):
            child = action.get(child_attr)
            if child:
                yield from _iter_actions(child)


def _rewrite_action(
    action: dict[str, Any],
    rename_map: dict[tuple[str, str | None, str], str],
    *,
    default_conn: str | None,
) -> None:
    """In-place: replace any query name on a run_query / navigate action that matches
    the rename map."""
    a_type = action.get("type")
    a_conn = action.get("connector") or default_conn
    if a_type == "run_query" and "query" in action and action["query"]:
        action["query"] = _query_rename(rename_map, a_conn, action["query"])
    elif a_type == "navigate" and "to" in action and action["to"]:
        action["to"] = _query_rename(rename_map, a_conn, action["to"])


def _rewrite_chart(
    cfg: dict[str, Any],
    rename_map: dict[tuple[str, str | None, str], str],
) -> dict[str, Any]:
    """Rewrite a Chart's query reference — single ``query`` field on the chart's
    own connector (chart.scope is the connector)."""
    out = _deep_copy(cfg)
    if "query" in out and out["query"]:
        out["query"] = _query_rename(rename_map, out.get("connector"), out["query"])
    return out


def _rewrite_dashboard(
    cfg: dict[str, Any],
    rename_map: dict[tuple[str, str | None, str], str],
) -> dict[str, Any]:
    """Rewrite a Dashboard's widget + filter query references. The dashboard has a
    top-level ``connector`` default; each widget / filter can override."""
    out = _deep_copy(cfg)
    dash_conn = out.get("connector")
    for w in out.get("widgets") or []:
        if not isinstance(w, dict):
            continue
        w_conn = w.get("connector") or dash_conn
        if "query" in w and w["query"]:
            w["query"] = _query_rename(rename_map, w_conn, w["query"])
    for filt in out.get("filters") or []:
        if not isinstance(filt, dict):
            continue
        opts = filt.get("options")
        if isinstance(opts, dict) and "query" in opts and opts["query"]:
            opts["query"] = _query_rename(rename_map, opts.get("connector") or dash_conn, opts["query"])
    return out
