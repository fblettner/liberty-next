"""Cross-file top-level-key renames — the operation behind ``POST /admin/config/rename``.

The structured config builders (Settings → Pools / Connectors / Dictionary / Menus / Screens /
Dashboards) edit each file's body in place, but a top-level key (a *connector name*, a
*sequence id*, a *lookup id*, an *app name*) is referenced from several other files. Renaming
``[connectors.nomasx1]`` by hand means hunting every ``connector = "nomasx1"`` in
``screens.toml`` / ``menus.toml`` / ``dictionary.toml`` / ``dashboards.toml`` / ``charts.toml``
and updating each — tedious and error-prone.

This module performs the rename **atomically across every affected file** while preserving
comments + formatting via ``tomlkit``. It validates the resulting documents against the
matching Pydantic schemas before writing anything to disk (an in-memory dry run is the only
way to catch a rename that would create a name collision or break a referenced key). On
success it returns a :class:`RenameResult` recording exactly what was changed per file so the
caller can show the operator a summary.

What's covered (Phase 7 loose-ends slice):

* :func:`rename_connector` — the highest-value case. A connector's top-level key in
  ``connectors.toml`` (``[connectors.<old>]``) renames; every ``connector = "<old>"`` field
  value across :file:`screens.toml`, :file:`menus.toml`, :file:`dashboards.toml`,
  :file:`charts.toml` updates; the per-connector dictionary scope (``[connectors.<old>.…]``
  in :file:`dictionary.toml`) renames; ``LookupDef.connector`` / ``SequenceDef.connector``
  references (in both ``connectors.toml`` and ``dictionary.toml``) update.

* :func:`rename_sequence` / :func:`rename_lookup` — dictionary-side keys plus the
  ``DictionaryEntry.rules_values`` references that point at the renamed sequence / lookup.
  Optional ``scope`` (a connector name) narrows the rename to a per-connector overlay
  (``[connectors.<scope>.sequences.<old>]`` etc.); ``scope = None`` operates on the shared
  ``[sequences.<old>]`` top-level.
* :func:`rename_screen_app` — renames a ``[screens.<old>]`` top-level key plus the matching
  ``[menus.<old>]`` block when one exists (apps and connectors are distinct concepts, but in
  practice a screen app and its menu share a name; renaming one without the other usually
  leaks).
* :func:`rename_dictionary_entry` — the deepest walk. ``[entries.<old>]`` (shared or scoped)
  renames; every ``Screen.columns[].dd`` / ``PromptField.dd`` (in actions' prompt_fields)
  across :file:`screens.toml` updates; ``SequenceDef.dd_id`` + ``LookupDef.return_params``
  references in :file:`dictionary.toml` update.
"""

from __future__ import annotations

import re
import tomllib
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


# A v2-friendly identifier — same rules slugify already enforces on migration. We keep the
# regex tight (no leading digits, no special chars) so a renamed connector behaves identically
# everywhere — TOML key, Pydantic discriminator, permission string (``sql:<connector>:<query>``),
# frontend route segment, etc.
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class RenameError(ValueError):
    """Raised when the rename can't proceed (collision / invalid name / target not found)."""


@dataclass
class RenameResult:
    """What ``rename_connector`` (and future ``rename_<kind>``) actually changed.

    ``files`` maps each affected config path to a count of touched references — non-zero means
    that file was rewritten. ``warnings`` collects non-fatal observations (a file that doesn't
    exist on disk is silently skipped; a file that exists but doesn't carry any reference is
    left untouched + reported as ``0``). The caller (the route layer) returns this verbatim to
    the operator so they see "12 refs touched across 4 files" or "no refs found — you can
    delete the connector definition manually"."""

    kind: str
    old_name: str
    new_name: str
    files: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def total_refs(self) -> int:
        return sum(self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "old_name": self.old_name,
            "new_name": self.new_name,
            "files": self.files,
            "warnings": self.warnings,
            "total_refs": self.total_refs(),
        }


# ── connector rename ──────────────────────────────────────────────────────────────────────


def validate_identifier(name: str, *, what: str = "name") -> None:
    """Reject anything that isn't a v2 identifier. v1's app names were free-form (mixed case,
    dashes, spaces); v2 sticks to ``[a-z][a-z0-9_]*`` so the name reads the same as a TOML key,
    a permission string, and a URL segment — consistency over flexibility."""
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise RenameError(
            f"invalid {what}: {name!r} — must match {_IDENT_RE.pattern} "
            "(lowercase letters, digits, underscore; leading letter)"
        )


def rename_connector(
    old: str,
    new: str,
    *,
    connectors_path: Path,
    screens_path: Path,
    menus_path: Path,
    dictionary_path: Path,
    dashboards_path: Path | None = None,
    charts_path: Path | None = None,
) -> RenameResult:
    """Rename a connector + every cross-file reference. See module docstring for scope.

    Two-pass strategy:

    1. **In-memory rewrite** — load each file via ``tomlkit``, traverse the body, update every
       matching key / field value. Count touched refs per file.
    2. **Validation** — parse each rewritten document back through its Pydantic schema. A
       collision (e.g. the new connector name already exists) or a now-invalid reference
       fails the whole operation; nothing is written to disk.
    3. **Write** — only when every file validates, write all of them out.

    The connector name MUST exist in ``connectors.toml`` for the rename to proceed. The new
    name MUST NOT collide with any existing connector. References in other files MAY exist
    (an operator may rename a connector that hasn't been wired into a screen yet); the rename
    counts the touched refs but doesn't require any to be present.
    """
    if old == new:
        raise RenameError(f"old and new names are identical ({old!r}) — nothing to do")
    validate_identifier(old, what="old name")
    validate_identifier(new, what="new name")

    result = RenameResult(kind="connector", old_name=old, new_name=new)

    # Pre-load every doc we'll touch (file missing → empty doc; that's fine, we just won't
    # write anything for it). The list below pairs each path with the in-memory document we'll
    # rewrite and the Pydantic validator that has to accept the result.
    #
    # connectors.toml stays on tomlkit (small file — comment preservation is cheap and useful
    # for hand-edited pools / credentials). Everything else (screens, menus, dictionary,
    # dashboards, charts) goes through tomllib + tomli_w because tomlkit's parse + dumps is
    # O(n²) and chokes on the 15k+-line screens.toml / dictionary.toml the visual builders
    # generate. The rewrite helpers walk via ``isinstance(node, dict|list)`` so they don't
    # care whether the tree came from tomlkit or tomllib. See [[project-tomlkit-o2-cliff]].
    _BIG_LABELS = {"screens", "menus", "dictionary", "dashboards", "charts"}
    docs: dict[str, tuple[Path, Any]] = {}
    for label, path in (
        ("connectors", connectors_path),
        ("screens", screens_path),
        ("menus", menus_path),
        ("dictionary", dictionary_path),
        ("dashboards", dashboards_path),
        ("charts", charts_path),
    ):
        if path is None:
            continue
        empty: Any = {} if label in _BIG_LABELS else tomlkit.document()
        if path.exists() and path.read_text(encoding="utf-8").strip():
            text = path.read_text(encoding="utf-8")
            docs[label] = (
                path,
                tomllib.loads(text) if label in _BIG_LABELS else tomlkit.parse(text),
            )
        else:
            docs[label] = (path, empty)
            result.files[str(path)] = 0   # file absent → reported as zero, not skipped

    # 1) connectors.toml — rename the top-level [connectors.<old>] table + update any
    #    LookupDef.connector / SequenceDef.connector references in other connectors' lookups
    #    or sequences blocks.
    conn_path, conn_doc = docs["connectors"]
    n = _rewrite_connectors_doc(conn_doc, old=old, new=new)
    result.files[str(conn_path)] = n
    if n == 0:
        raise RenameError(
            f"connector {old!r} not found in {conn_path} — nothing to rename. "
            "Are you sure the connector exists? (Check Settings → Connectors.)"
        )

    # 2) screens.toml — every Screen.connector / NestedFormTab.connector / NestedTableTab.connector
    #    / Action variants' connector / row_click_connector field value.
    scr_path, scr_doc = docs["screens"]
    result.files[str(scr_path)] = _rewrite_screens_doc(scr_doc, old=old, new=new)

    # 3) menus.toml — every MenuItem.connector value (the operator may also want to rename the
    #    [menus.<app>] key when app == connector; that's a separate operation).
    menu_path, menu_doc = docs["menus"]
    result.files[str(menu_path)] = _rewrite_menus_doc(menu_doc, old=old, new=new)
    if old in (menu_doc.get("menus") or {}):
        result.warnings.append(
            f"menus.toml carries a [menus.{old}] app block — that's a separate concept from the "
            "connector name; rename it explicitly if you also want the app key to change."
        )

    # 4) dictionary.toml — rename the per-connector scope [connectors.<old>.*] + update any
    #    lookups.X.connector / sequences.X.connector that point at the old name.
    dict_path, dict_doc = docs["dictionary"]
    result.files[str(dict_path)] = _rewrite_dictionary_doc(dict_doc, old=old, new=new)

    # 5) dashboards.toml — every ChartWidget / KpiWidget / DashboardFilterOptions connector.
    if "dashboards" in docs:
        dash_path, dash_doc = docs["dashboards"]
        result.files[str(dash_path)] = _rewrite_dashboards_doc(dash_doc, old=old, new=new)

    # 6) charts.toml — ChartDef.connector (saved charts).
    if "charts" in docs:
        chart_path, chart_doc = docs["charts"]
        result.files[str(chart_path)] = _rewrite_charts_doc(chart_doc, old=old, new=new)

    # ── validation pass — none of the in-memory docs get written until all parse ──
    _validate("connectors", conn_doc, parse_connectors, conn_path)
    if result.files.get(str(scr_path)):
        _validate("screens", scr_doc, parse_screens, scr_path)
    if result.files.get(str(menu_path)):
        _validate("menus", menu_doc, parse_menus, menu_path)
    if result.files.get(str(dict_path)):
        _validate("dictionary", dict_doc, parse_dictionary, dict_path)
    if "dashboards" in docs and result.files.get(str(docs["dashboards"][0])):
        _validate("dashboards", docs["dashboards"][1], parse_dashboards, docs["dashboards"][0])
    if "charts" in docs and result.files.get(str(docs["charts"][0])):
        _validate("charts", docs["charts"][1], parse_charts, docs["charts"][0])

    # ── write pass — every touched file lands in one batch ──
    # Big files round-trip via tomli_w (lose comments — operators don't hand-edit screens.toml
    # / dictionary.toml anyway; the structured UI is the source of truth). Small files stay on
    # tomlkit so connectors.toml keeps its pool / credential comments intact.
    import tomli_w
    for label, (path, doc) in docs.items():
        if not result.files.get(str(path), 0):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if label in _BIG_LABELS:
            path.write_text(tomli_w.dumps(doc), encoding="utf-8")
        else:
            path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    return result


def _validate(label: str, doc: Any, parser: Any, path: Path) -> None:
    """Re-parse the rewritten document and run it through the matching Pydantic parser. Any
    error becomes a :class:`RenameError` referencing the file + the underlying message.

    Accepts either a tomlkit ``TOMLDocument`` (the comment-preserving path used by connector
    / screen-app / query renames) OR a plain ``dict`` (the dictionary-rename path, which uses
    tomllib + tomli-w to dodge tomlkit's O(n²) cliff on large dictionary.toml files — same
    rationale as :func:`liberty.web.admin.put_dictionary_parsed`)."""
    try:
        if isinstance(doc, dict) and not isinstance(doc, tomlkit.TOMLDocument):
            import tomli_w
            parser(tomllib.loads(tomli_w.dumps(doc)))
        else:
            parser(tomllib.loads(tomlkit.dumps(doc)))
    except Exception as exc:                 # noqa: BLE001 — surface any error to the operator
        raise RenameError(f"rename would make {label} ({path}) invalid: {exc}") from exc


# ── per-file rewriters ─────────────────────────────────────────────────────────────────────


def _rewrite_connectors_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    """Rewrite the top-level ``[connectors.<old>]`` subtree to use *new*. That's the only
    connector-name reference inside connectors.toml — lookups + sequences (which can carry
    ``LookupDef.connector`` / ``SequenceDef.connector`` cross-references) live in
    dictionary.toml, not here.

    Returns the number of touched references — 0 means the connector wasn't found (the caller
    raises in that case)."""
    conns = doc.get("connectors")
    if not conns or old not in conns:
        return 0
    if new in conns:
        raise RenameError(
            f"connector {new!r} already exists — pick another name "
            f"(or delete {new!r} first if you really mean to replace it)"
        )
    # tomlkit preserves comment/formatting on a value; assigning to a new key + del on the
    # old key re-renders the section header. Same convention put_pools / put_connectors uses.
    conns[new] = conns[old]
    del conns[old]
    return 1


def _rewrite_screens_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    """Walk every screen + nested tab + action chain + row_click_connector and update any
    ``connector = "<old>"`` field. Returns the count of touched fields. The walk is recursive
    so deeply-nested ChainAction / IfAction / LoopAction steps + nested-form-tab inner actions
    all get visited."""
    return _replace_connector_field_recursive(doc.get("screens"), old=old, new=new)


def _rewrite_menus_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    n = _replace_connector_field_recursive(doc.get("menus"), old=old, new=new)
    # Dashboards are scoped (``[dashboards.<scope>.<id>]``) and their public id — what a
    # ``type = "dashboard"`` menu item targets — is the qualified ``<scope>.<id>``. Renaming the
    # connector renames the dashboard's scope, so any menu target ``"<old>.<id>"`` must follow.
    n += _rewrite_dashboard_targets(doc.get("menus"), old=old, new=new)
    return n


def _rewrite_dashboard_targets(node: Any, *, old: str, new: str) -> int:
    """Walk menu items and rewrite ``target = "<old>.<id>"`` → ``"<new>.<id>"`` on
    ``type = "dashboard"`` leaves (the dashboard's qualified id changed with its scope)."""
    n = 0
    if isinstance(node, dict):
        if node.get("type") == "dashboard" and isinstance(node.get("target"), str):
            scope, sep, rest = node["target"].partition(".")
            if sep and scope == old:
                node["target"] = f"{new}.{rest}"
                n += 1
        for v in node.values():
            n += _rewrite_dashboard_targets(v, old=old, new=new)
    elif isinstance(node, list):
        for v in node:
            n += _rewrite_dashboard_targets(v, old=old, new=new)
    return n


def _rewrite_dictionary_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    """Two operations on the dictionary:

    1. Rename the per-connector scope ``[connectors.<old>]`` → ``[connectors.<new>]``. v1's
       per-app dictionaries became v2's per-connector overlays; renaming the connector means
       renaming this scope too.
    2. Walk every lookup / sequence definition (shared + per-connector scoped) and update any
       ``connector = "<old>"`` field — same as :func:`_rewrite_connectors_doc` does for
       cross-connector lookup references in connectors.toml.
    """
    n = 0
    conns = doc.get("connectors")
    if isinstance(conns, dict) and old in conns:
        if new in conns:
            raise RenameError(
                f"dictionary already has a [connectors.{new}] scope — rename would clash"
            )
        conns[new] = conns[old]
        del conns[old]
        n += 1
    # Then update lookups / sequences anywhere they sit (shared OR connector-scoped).
    n += _replace_connector_field_recursive(doc.get("lookups"), old=old, new=new)
    n += _replace_connector_field_recursive(doc.get("sequences"), old=old, new=new)
    n += _replace_connector_field_recursive(conns, old=old, new=new)   # per-connector scopes
    return n


def _rewrite_dashboards_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    """Dashboards are scoped (``[dashboards.<scope>.<id>]``). Two operations:

    1. Rename the owning-scope key ``[dashboards.<old>]`` → ``[dashboards.<new>]`` when a
       dashboard belongs to the renamed connector.
    2. Walk every widget + DashboardFilterOptions and update ``connector = "<old>"`` (inline
       chart / KPI / table / filter all carry one) — a widget can read *any* connector, so this
       is independent of the dashboard's own scope.
    """
    dashboards = doc.get("dashboards")
    n = _rename_scope_key(dashboards, old=old, new=new, label="dashboards.toml")
    n += _replace_connector_field_recursive(dashboards, old=old, new=new)
    return n


def _rewrite_charts_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    """Charts are scoped (``[charts.<scope>.<id>]``) — the connector *is* the scope key. Rename
    ``[charts.<old>]`` → ``[charts.<new>]`` (the body has no ``connector`` field anymore)."""
    charts = doc.get("charts")
    n = _rename_scope_key(charts, old=old, new=new, label="charts.toml")
    n += _replace_connector_field_recursive(charts, old=old, new=new)
    return n


def _rename_scope_key(table: Any, *, old: str, new: str, label: str) -> int:
    """Rename a top-level *scope* key ``table[old]`` → ``table[new]`` (charts / dashboards are keyed
    by their connector scope). Returns 1 when renamed, 0 when ``old`` isn't present. Raises on a
    collision with an existing ``new`` key (same guard the connector / dictionary-scope rename use)."""
    if not isinstance(table, dict) or old not in table:
        return 0
    if new in table:
        raise RenameError(f"{label} already has a [{new}] scope — rename would clash")
    table[new] = table[old]
    del table[old]
    return 1


def _replace_connector_field_recursive(node: Any, *, old: str, new: str) -> int:
    """Walk a tomlkit / dict / list tree and replace ``connector = "<old>"`` (or
    ``row_click_connector = "<old>"``) wherever it appears. Returns the count of touched
    fields. Both ``connector`` and ``row_click_connector`` are connector-name fields in v2;
    listing them explicitly makes the intent obvious (a future ``some_other_field`` won't
    accidentally rename — only the listed keys do)."""
    n = 0
    if isinstance(node, dict):
        for key in ("connector", "row_click_connector"):
            if node.get(key) == old:
                node[key] = new
                n += 1
        for v in node.values():
            n += _replace_connector_field_recursive(v, old=old, new=new)
    elif isinstance(node, list):
        for v in node:
            n += _replace_connector_field_recursive(v, old=old, new=new)
    return n


# ── sequence / lookup rename ─────────────────────────────────────────────────────────────────


def rename_sequence(
    old: str,
    new: str,
    *,
    dictionary_path: Path,
    scope: str | None = None,
) -> RenameResult:
    """Rename a sequence id. With ``scope = None`` operates on the shared
    ``[sequences.<old>]`` top-level; with ``scope = "<conn>"`` operates on the per-connector
    overlay ``[connectors.<scope>.sequences.<old>]``. The matching ``DictionaryEntry.rules_values``
    references (where ``rules == "SEQUENCE"`` or ``"NN"``) update in the *same scope* — a
    sequence id is dictionary-internal so we don't need to scan other config files."""
    return _rename_dict_collection(
        kind="sequence",
        coll="sequences",
        ref_rules={"SEQUENCE", "NN"},
        old=old, new=new,
        dictionary_path=dictionary_path,
        scope=scope,
    )


def rename_lookup(
    old: str,
    new: str,
    *,
    dictionary_path: Path,
    scope: str | None = None,
) -> RenameResult:
    """Same shape as :func:`rename_sequence` — renames ``[lookups.<old>]`` (shared or scoped)
    + every ``DictionaryEntry.rules_values`` whose ``rules == "LOOKUP"`` matches. Lookups are
    referenced exclusively from dictionary entries (the SQL connector resolves them via the
    entry's rules_values), so no cross-file walk is needed."""
    return _rename_dict_collection(
        kind="lookup",
        coll="lookups",
        ref_rules={"LOOKUP"},
        old=old, new=new,
        dictionary_path=dictionary_path,
        scope=scope,
    )


def _rename_dict_collection(
    *,
    kind: str,
    coll: str,
    ref_rules: set[str],
    old: str,
    new: str,
    dictionary_path: Path,
    scope: str | None,
) -> RenameResult:
    """Shared implementation for :func:`rename_sequence` / :func:`rename_lookup` (and any
    future "dictionary-internal" collection rename). The two functions only differ in:

    * which subtable holds the definitions (``sequences`` vs ``lookups``), and
    * which ``DictionaryEntry.rules`` values point at this kind (``SEQUENCE``/``NN`` for
      sequences, ``LOOKUP`` for lookups).
    """
    if old == new:
        raise RenameError(f"old and new names are identical ({old!r}) — nothing to do")
    # v2 id rules — same as connector names. (Sequence / lookup ids are typically integer-like
    # strings in v1 migrations — "1", "2" — but operators may want to rename them to readable
    # names like ``user_status``; we allow any v2 identifier here.)
    validate_identifier(new, what="new name")

    result = RenameResult(kind=kind, old_name=old, new_name=new)
    if scope is not None:
        validate_identifier(scope, what="scope")

    # Pre-load the dictionary doc. A missing / empty dictionary file means there's nothing to
    # rename — surface a useful error rather than silently no-op.
    if not dictionary_path.exists() or not dictionary_path.read_text(encoding="utf-8").strip():
        raise RenameError(f"dictionary file {dictionary_path} is missing or empty")
    # tomllib instead of tomlkit — tomlkit.parse is O(n²)-ish on big nested-table files
    # (5k+ lines / 100 KB+) and takes minutes on a real JDE-loaded dictionary.toml. The
    # PUT path already documented + dodged this (see put_dictionary_parsed in admin.py).
    # The rename path was still using tomlkit, inheriting the same hang. Dictionary is
    # generated content (liberty-migrate dictionary) — no operator hand-edit comments to
    # protect, so the lossy round-trip is fine here too.
    doc = tomllib.loads(dictionary_path.read_text(encoding="utf-8"))

    # Resolve the section we'll mutate. Shared = doc[<coll>]; scoped = doc[connectors][scope][<coll>].
    if scope is None:
        section = doc.get(coll)
        entries_section = doc.get("entries")
        scope_label = "shared"
    else:
        conns = doc.get("connectors") or {}
        scope_table = conns.get(scope) if isinstance(conns, dict) else None
        if not isinstance(scope_table, dict):
            raise RenameError(
                f"connector scope {scope!r} not found in dictionary — add the section first "
                f"or rename without a scope to target the shared [{coll}.<id>] tables"
            )
        section = scope_table.get(coll)
        entries_section = scope_table.get("entries")
        scope_label = f"connectors.{scope}"

    if not isinstance(section, dict) or old not in section:
        raise RenameError(
            f"{kind} {old!r} not found under [{scope_label}.{coll}] — nothing to rename. "
            "(Pass a scope=<connector> to operate on a per-connector overlay.)"
        )
    if new in section:
        raise RenameError(
            f"{kind} {new!r} already exists under [{scope_label}.{coll}] — pick another name"
        )

    # 1) rename the definition key itself.
    section[new] = section[old]
    del section[old]
    n = 1

    # 2) update DictionaryEntry.rules_values references within the same scope. Entries from
    #    other scopes are intentionally left alone — they may carry the same id (a v1 migration
    #    can produce two scopes referencing different sequences both numbered "1" — narrower
    #    rename is the safer default).
    if isinstance(entries_section, dict):
        for _entry_key, entry in entries_section.items():
            if not isinstance(entry, dict):
                continue
            rules = entry.get("rules")
            if isinstance(rules, str) and rules.upper() in ref_rules and entry.get("rules_values") == old:
                entry["rules_values"] = new
                n += 1

    # Validate the rewritten doc before writing.
    _validate("dictionary", doc, parse_dictionary, dictionary_path)
    import tomli_w
    dictionary_path.write_text(tomli_w.dumps(doc), encoding="utf-8")

    result.files[str(dictionary_path)] = n
    return result


# ── screen-app rename ────────────────────────────────────────────────────────────────────────


def rename_screen_app(
    old: str,
    new: str,
    *,
    screens_path: Path,
    menus_path: Path,
) -> RenameResult:
    """Rename a screen *app* key — the top-level dict key in :file:`screens.toml` and the
    matching ``[menus.<old>]`` block when one exists (apps and connectors are distinct concepts
    but in practice they share a name; renaming the screens app without the menu would leave
    a dead navigation root).

    Does **not** touch ``Screen.connector`` / ``MenuItem.connector`` field values — those are
    *connector* references, which the operator renames via :func:`rename_connector` (a
    separate operation; the connector and the screen-app can diverge in name).
    """
    if old == new:
        raise RenameError(f"old and new names are identical ({old!r}) — nothing to do")
    validate_identifier(new, what="new name")

    result = RenameResult(kind="screen_app", old_name=old, new_name=new)

    # screens.toml — the required side. The app MUST exist here (an app key with no screens
    # makes no sense; if the operator wants to "rename" the menu app only, that's a future
    # ``rename_menu_app`` op).
    if not screens_path.exists() or not screens_path.read_text(encoding="utf-8").strip():
        raise RenameError(f"screens file {screens_path} is missing or empty")
    scr_doc = tomlkit.parse(screens_path.read_text(encoding="utf-8"))
    screens_table = scr_doc.get("screens") or {}
    if not isinstance(screens_table, dict) or old not in screens_table:
        raise RenameError(f"screens app {old!r} not found in {screens_path}")
    if new in screens_table:
        raise RenameError(f"screens app {new!r} already exists — pick another name")
    screens_table[new] = screens_table[old]
    del screens_table[old]
    result.files[str(screens_path)] = 1

    # menus.toml — same app key OR no-op + warning. Renaming the menu when the operator just
    # wanted a rename of the screen side would surprise them; renaming both is the typical
    # case, so do it but tell them what happened.
    if menus_path.exists() and menus_path.read_text(encoding="utf-8").strip():
        menu_doc = tomlkit.parse(menus_path.read_text(encoding="utf-8"))
        menus_table = menu_doc.get("menus") or {}
        if isinstance(menus_table, dict) and old in menus_table:
            if new in menus_table:
                raise RenameError(f"menus app {new!r} already exists — pick another name")
            menus_table[new] = menus_table[old]
            del menus_table[old]
            result.files[str(menus_path)] = 1
            _validate("menus", menu_doc, parse_menus, menus_path)
            menus_path.write_text(tomlkit.dumps(menu_doc), encoding="utf-8")
        else:
            result.warnings.append(
                f"no matching [menus.{old}] block in {menus_path} — left untouched."
            )
            result.files[str(menus_path)] = 0
    else:
        result.files[str(menus_path)] = 0

    _validate("screens", scr_doc, parse_screens, screens_path)
    screens_path.write_text(tomlkit.dumps(scr_doc), encoding="utf-8")
    return result


# ── dictionary entry rename ─────────────────────────────────────────────────────────────────


def rename_dictionary_entry(
    old: str,
    new: str,
    *,
    dictionary_path: Path,
    screens_path: Path,
    scope: str | None = None,
) -> RenameResult:
    """Rename a dictionary entry key — ``[entries.<old>]`` (shared) or
    ``[connectors.<scope>.entries.<old>]`` — plus every reference to it:

    * In :file:`dictionary.toml`: ``SequenceDef.dd_id`` (same scope) and
      ``LookupDef.return_params`` (same scope) entries.
    * In :file:`screens.toml`: every ``ColumnHint.dd == old`` (Phase 3: columns live on
      ``Screen.columns``) and every ``PromptField.dd == old`` (inside actions' prompt_fields).
      The screens-side walk is scope-blind — dd references are free strings and there's no
      scope mechanism on a ``Screen.columns[].dd`` field. The operator sees the touched-refs
      count in the result; if a refs target was a different scope's entry with the same name,
      they can manually revert via the builder.

    No cross-file ref in :file:`menus.toml` / :file:`dashboards.toml` / :file:`charts.toml`
    points at a dd entry — they reference queries / endpoints / charts by name.
    """
    if old == new:
        raise RenameError(f"old and new names are identical ({old!r}) — nothing to do")
    # Dictionary entry keys are typically uppercase (v1's dd_id convention: APPS_ID,
    # USR_NAME, etc.). Accept either case; the identifier regex would reject uppercase, so
    # use a looser shape for entry keys.
    if not isinstance(new, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new):
        raise RenameError(f"invalid new name: {new!r} — must be a valid identifier (letters / digits / underscore; leading letter)")

    result = RenameResult(kind="dictionary_entry", old_name=old, new_name=new)
    if scope is not None:
        validate_identifier(scope, what="scope")

    # 1) dictionary.toml — rename the key + update SequenceDef.dd_id + LookupDef.return_params.
    if not dictionary_path.exists() or not dictionary_path.read_text(encoding="utf-8").strip():
        raise RenameError(f"dictionary file {dictionary_path} is missing or empty")
    # tomllib + tomli-w instead of tomlkit — same O(n²) rationale as the lookup / sequence
    # rename above. See put_dictionary_parsed in admin.py for the original write of this fix.
    d_doc = tomllib.loads(dictionary_path.read_text(encoding="utf-8"))

    if scope is None:
        entries_section = d_doc.get("entries")
        scope_table = d_doc
        scope_label = "shared"
    else:
        conns = d_doc.get("connectors") or {}
        scope_table = conns.get(scope) if isinstance(conns, dict) else None
        if not isinstance(scope_table, dict):
            raise RenameError(f"connector scope {scope!r} not found in dictionary")
        entries_section = scope_table.get("entries")
        scope_label = f"connectors.{scope}"

    if not isinstance(entries_section, dict) or old not in entries_section:
        raise RenameError(
            f"entry {old!r} not found under [{scope_label}.entries] — nothing to rename"
        )
    if new in entries_section:
        raise RenameError(
            f"entry {new!r} already exists under [{scope_label}.entries] — pick another name"
        )
    entries_section[new] = entries_section[old]
    del entries_section[old]
    n_dict = 1

    # Update SequenceDef.dd_id + LookupDef.return_params within the same scope. Sequences's
    # dd_id is a single string; LookupDef.return_params is a list (each element is a dd_id).
    sequences = scope_table.get("sequences") if isinstance(scope_table, dict) else None
    if isinstance(sequences, dict):
        for _sid, seq in sequences.items():
            if isinstance(seq, dict) and seq.get("dd_id") == old:
                seq["dd_id"] = new
                n_dict += 1
    lookups = scope_table.get("lookups") if isinstance(scope_table, dict) else None
    if isinstance(lookups, dict):
        for _lid, lk in lookups.items():
            if not isinstance(lk, dict):
                continue
            rp = lk.get("return_params")
            if isinstance(rp, list):
                for i, p in enumerate(rp):
                    if p == old:
                        rp[i] = new
                        n_dict += 1
    _validate("dictionary", d_doc, parse_dictionary, dictionary_path)
    result.files[str(dictionary_path)] = n_dict

    # 2) screens.toml — walk every ColumnHint.dd / PromptField.dd field. Both live somewhere
    #    deep under [screens.<app>.<id>] — recursive walk is simplest. tomllib + tomli-w for
    #    the same perf reason: a JDE-loaded screens.toml is several thousand lines.
    n_scr = 0
    if screens_path.exists() and screens_path.read_text(encoding="utf-8").strip():
        import tomli_w as _tomli_w_screens
        s_doc = tomllib.loads(screens_path.read_text(encoding="utf-8"))
        n_scr = _replace_dd_field_recursive(s_doc.get("screens"), old=old, new=new)
        if n_scr:
            _validate("screens", s_doc, parse_screens, screens_path)
            screens_path.write_text(_tomli_w_screens.dumps(s_doc), encoding="utf-8")
        result.files[str(screens_path)] = n_scr
    else:
        result.files[str(screens_path)] = 0

    import tomli_w
    dictionary_path.write_text(tomli_w.dumps(d_doc), encoding="utf-8")
    return result


def _replace_dd_field_recursive(node: Any, *, old: str, new: str) -> int:
    """Recursive replacement of ``dd = "<old>"`` field values in any nested object. Used by
    :func:`rename_dictionary_entry` to walk screens.toml — ColumnHint + PromptField both carry
    a ``dd`` field, and they appear under several levels of nesting (per-screen columns,
    dialog tab nested actions' prompt_fields, screen-level actions' prompt_fields, …)."""
    n = 0
    if isinstance(node, dict):
        if node.get("dd") == old:
            node["dd"] = new
            n += 1
        for v in node.values():
            n += _replace_dd_field_recursive(v, old=old, new=new)
    elif isinstance(node, list):
        for v in node:
            n += _replace_dd_field_recursive(v, old=old, new=new)
    return n


# ── query rename (connector-scoped) ─────────────────────────────────────────────────────────
# A query is identified by ``(connector, name)``. Renaming it rewrites the [[connectors.<c>.queries]]
# entry + every cross-file reference *scoped to that connector*: screens' read/update/insert/
# delete_query + RunQueryAction.query + NavigateAction.to + ExportSheet.query, dictionary
# sequence/lookup ``query``, chart ``query``, dashboard-widget ``query``, and sql menu leaves'
# ``target``. Scoping matters because two connectors may share a query name — we walk the tree
# tracking the effective connector (a node's own ``connector`` field, else inherited from its
# parent screen/scope/app) and only rename a ref whose context matches the target connector.
_QUERY_REF_FIELDS = ("read_query", "update_query", "insert_query", "delete_query", "query", "to")


def _replace_query_refs(node: Any, *, mapping: dict[str, str], target_conn: str, default_conn: str | None) -> int:
    """Walk a tomlkit/dict/list tree, threading the effective connector context, and rename any
    query-reference field whose value is in ``mapping`` when the context matches ``target_conn``."""
    n = 0
    if isinstance(node, dict):
        own = node.get("connector")
        ctx = own if isinstance(own, str) and own else default_conn
        if ctx == target_conn:
            for f in _QUERY_REF_FIELDS:
                v = node.get(f)
                if isinstance(v, str) and v in mapping:
                    node[f] = mapping[v]
                    n += 1
            # A menu leaf's ``target`` is a query name only when it's a SQL ("query") leaf.
            if node.get("type") == "query":
                tv = node.get("target")
                if isinstance(tv, str) and tv in mapping:
                    node["target"] = mapping[tv]
                    n += 1
        for v in node.values():
            n += _replace_query_refs(v, mapping=mapping, target_conn=target_conn, default_conn=ctx)
    elif isinstance(node, list):
        for v in node:
            n += _replace_query_refs(v, mapping=mapping, target_conn=target_conn, default_conn=default_conn)
    return n


def _replace_query_refs_by_scope(scoped: Any, *, mapping: dict[str, str], target_conn: str) -> int:
    """For scope-keyed tables (``[charts.<scope>.<id>]`` / ``[dashboards.<scope>.<id>]``): walk each
    scope's subtree with that scope as the *default* connector context, so query refs in bodies that
    don't carry their own ``connector`` field (charts) are renamed when the scope is ``target_conn``;
    a widget's explicit ``connector`` still overrides (cross-connector dashboard widgets)."""
    n = 0
    if isinstance(scoped, dict):
        for scope, by_id in scoped.items():
            n += _replace_query_refs(
                by_id, mapping=mapping, target_conn=target_conn,
                default_conn=scope if isinstance(scope, str) else None,
            )
    return n


# The three flat query sections that carry standalone ``name``d entries. Tables live in their
# own ``tables`` section and are renamed by base name (see ``_rewrite_connectors_table_base``).
_FLAT_QUERY_SECTIONS = ("queries", "sequences", "lookups")


def _rewrite_connectors_queries(doc: tomlkit.TOMLDocument, *, connector: str, mapping: dict[str, str]) -> int:
    """Rename matching queries by ``name`` across the connector's flat query sections —
    ``queries`` (custom), ``sequences`` and ``lookups``. Shape-tolerant: the legacy flat shape
    kept every query (including sequences / lookups) in ``queries``, so walking all three covers
    both. Raises on a collision with an existing (non-renamed) name. Returns the count renamed
    (0 → caller raises)."""
    conns = doc.get("connectors")
    if not isinstance(conns, dict) or connector not in conns:
        raise RenameError(f"connector {connector!r} not found in connectors.toml")
    conn = conns[connector]
    existing: set[str | None] = set()
    for section in _FLAT_QUERY_SECTIONS:
        lst = conn.get(section)
        if isinstance(lst, list):
            existing |= {q.get("name") for q in lst if isinstance(q, dict)}
    for new_name in mapping.values():
        if new_name in existing and new_name not in mapping:
            raise RenameError(f"query {new_name!r} already exists in connector {connector!r} — pick another name")
    n = 0
    for section in _FLAT_QUERY_SECTIONS:
        lst = conn.get(section)
        if not isinstance(lst, list):
            continue
        for q in lst:
            if isinstance(q, dict):
                name = q.get("name")
                if isinstance(name, str) and name in mapping:
                    q["name"] = mapping[name]
                    n += 1
    return n


def _rewrite_connectors_table_base(doc: tomlkit.TOMLDocument, *, connector: str, old_base: str, new_base: str) -> int:
    """Rename a CRUD table base inside ``[connectors.<connector>]``. Shape-tolerant:

      * **new shape** — rename the single ``[[connectors.<c>.tables]]`` entry whose ``name``
        field equals *old_base*. The ``<base>_<crud>`` slot names are synthesised, so there is
        nothing else to touch in connectors.toml.
      * **legacy shape** — no ``tables`` section; fall back to renaming the flat
        ``<old_base>_<crud>`` entries in ``queries`` via :func:`_rewrite_connectors_queries`.

    Either way the cross-file rewrite (screens / menus / dictionary / charts / dashboards) is
    driven by the ``<old>_<crud>`` → ``<new>_<crud>`` mapping the caller builds. Returns the
    count renamed in connectors.toml (0 → caller raises)."""
    conns = doc.get("connectors")
    if not isinstance(conns, dict) or connector not in conns:
        raise RenameError(f"connector {connector!r} not found in connectors.toml")
    conn = conns[connector]
    tables = conn.get("tables")
    if isinstance(tables, list) and tables:
        names = {t.get("name") for t in tables if isinstance(t, dict)}
        if new_base in names:
            raise RenameError(f"table {new_base!r} already exists in connector {connector!r} — pick another name")
        n = 0
        for t in tables:
            if isinstance(t, dict) and t.get("name") == old_base:
                t["name"] = new_base
                n += 1
        if n:
            return n
    # Legacy flat shape — rename the synthesised slot entries directly.
    mapping = {f"{old_base}_{c}": f"{new_base}_{c}" for c in ("get", "put", "post", "delete")}
    return _rewrite_connectors_queries(doc, connector=connector, mapping=mapping)


def _load_rename_docs(
    result: RenameResult,
    *,
    connectors_path: Path,
    screens_path: Path,
    menus_path: Path,
    dictionary_path: Path,
    charts_path: Path | None = None,
    dashboards_path: Path | None = None,
) -> dict[str, tuple[Path, tomlkit.TOMLDocument]]:
    """Pre-load every doc a query / table rename touches (missing file → empty doc, recorded as
    zero touches). Shared by :func:`rename_queries` and :func:`rename_table`."""
    docs: dict[str, tuple[Path, tomlkit.TOMLDocument]] = {}
    for label, path in (
        ("connectors", connectors_path), ("screens", screens_path), ("menus", menus_path),
        ("dictionary", dictionary_path), ("charts", charts_path), ("dashboards", dashboards_path),
    ):
        if path is None:
            continue
        if path.exists() and path.read_text(encoding="utf-8").strip():
            docs[label] = (path, tomlkit.parse(path.read_text(encoding="utf-8")))
        else:
            docs[label] = (path, tomlkit.document())
            result.files[str(path)] = 0
    return docs


def _rewrite_cross_file_refs_and_write(
    docs: dict[str, tuple[Path, tomlkit.TOMLDocument]],
    result: RenameResult,
    *,
    connector: str,
    mapping: dict[str, str],
) -> None:
    """Cross-file pass shared by query + table renames: rewrite every ``<old>`` → ``<new>``
    query reference in screens / menus / dictionary / charts / dashboards (the connectors.toml
    rewrite already happened in the caller), validate every touched doc, then write the batch.
    The reference machinery is name-based, so it's identical whether *mapping* came from a
    single query rename or a table's four synthesised ``<base>_<crud>`` slot names."""
    scr_path, scr_doc = docs["screens"]
    result.files[str(scr_path)] = _replace_query_refs(scr_doc.get("screens"), mapping=mapping, target_conn=connector, default_conn=None)
    menu_path, menu_doc = docs["menus"]
    n_menu = 0
    menus = menu_doc.get("menus")
    if isinstance(menus, dict):
        for app, acfg in menus.items():
            n_menu += _replace_query_refs(acfg, mapping=mapping, target_conn=connector, default_conn=app)
    result.files[str(menu_path)] = n_menu
    dict_path, dict_doc = docs["dictionary"]
    n_dict = _replace_query_refs(dict_doc.get("sequences"), mapping=mapping, target_conn=connector, default_conn=None)
    n_dict += _replace_query_refs(dict_doc.get("lookups"), mapping=mapping, target_conn=connector, default_conn=None)
    dconns = dict_doc.get("connectors")
    if isinstance(dconns, dict):
        for scope, scfg in dconns.items():
            n_dict += _replace_query_refs(scfg, mapping=mapping, target_conn=connector, default_conn=scope)
    result.files[str(dict_path)] = n_dict
    # charts / dashboards are keyed by their connector *scope* (``[charts.<scope>.<id>]``). Walk
    # each scope's subtree threading that scope as the default connector context — so a chart body
    # (no ``connector`` field anymore) inherits its scope, while a dashboard widget's explicit
    # ``connector`` still overrides (cross-connector widgets).
    if "charts" in docs:
        ch_path, ch_doc = docs["charts"]
        result.files[str(ch_path)] = _replace_query_refs_by_scope(ch_doc.get("charts"), mapping=mapping, target_conn=connector)
    if "dashboards" in docs:
        da_path, da_doc = docs["dashboards"]
        result.files[str(da_path)] = _replace_query_refs_by_scope(da_doc.get("dashboards"), mapping=mapping, target_conn=connector)

    _validate("connectors", docs["connectors"][1], parse_connectors, docs["connectors"][0])
    for label, parser in (("screens", parse_screens), ("menus", parse_menus), ("dictionary", parse_dictionary), ("charts", parse_charts), ("dashboards", parse_dashboards)):
        if label in docs and result.files.get(str(docs[label][0])):
            _validate(label, docs[label][1], parser, docs[label][0])

    for label, (path, doc) in docs.items():
        if not result.files.get(str(path), 0):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def rename_queries(
    connector: str,
    mapping: dict[str, str],
    *,
    kind: str,
    old_name: str,
    new_name: str,
    connectors_path: Path,
    screens_path: Path,
    menus_path: Path,
    dictionary_path: Path,
    charts_path: Path | None = None,
    dashboards_path: Path | None = None,
) -> RenameResult:
    """Rename one or more queries of *connector* (``mapping`` = old→new) + every cross-file
    reference, atomically. Same two-pass (rewrite → validate → write) strategy as
    :func:`rename_connector`. ``kind`` / ``old_name`` / ``new_name`` are for the result summary."""
    if any(o == nw for o, nw in mapping.items()):
        raise RenameError("old and new query names are identical — nothing to do")
    for nw in mapping.values():
        validate_identifier(nw, what="new query name")

    result = RenameResult(kind=kind, old_name=old_name, new_name=new_name)
    docs = _load_rename_docs(
        result, connectors_path=connectors_path, screens_path=screens_path, menus_path=menus_path,
        dictionary_path=dictionary_path, charts_path=charts_path, dashboards_path=dashboards_path,
    )
    conn_path, conn_doc = docs["connectors"]
    n = _rewrite_connectors_queries(conn_doc, connector=connector, mapping=mapping)
    result.files[str(conn_path)] = n
    if n == 0:
        raise RenameError(f"no matching query found in connector {connector!r} — nothing to rename")

    _rewrite_cross_file_refs_and_write(docs, result, connector=connector, mapping=mapping)
    return result


def rename_query(old: str, new: str, *, connector: str, **paths: Any) -> RenameResult:
    """Rename a single query (custom / sequence / lookup) + its references, scoped to *connector*."""
    return rename_queries(connector, {old: new}, kind="query", old_name=old, new_name=new, **paths)


def rename_table(
    old_base: str,
    new_base: str,
    *,
    connector: str,
    connectors_path: Path,
    screens_path: Path,
    menus_path: Path,
    dictionary_path: Path,
    charts_path: Path | None = None,
    dashboards_path: Path | None = None,
) -> RenameResult:
    """Rename a CRUD table base, atomically. In the new shape this renames the single
    ``[[connectors.<c>.tables]]`` ``name`` field; in the legacy shape it renames the flat
    ``<old_base>_<crud>`` query entries. Either way every cross-file binding that points at a
    ``<old_base>_<crud>`` slot is rewritten to ``<new_base>_<crud>``."""
    if old_base == new_base:
        raise RenameError(f"old and new base names are identical ({old_base!r}) — nothing to do")
    validate_identifier(new_base, what="new table name")

    result = RenameResult(kind="table", old_name=old_base, new_name=new_base)
    docs = _load_rename_docs(
        result, connectors_path=connectors_path, screens_path=screens_path, menus_path=menus_path,
        dictionary_path=dictionary_path, charts_path=charts_path, dashboards_path=dashboards_path,
    )
    conn_path, conn_doc = docs["connectors"]
    n = _rewrite_connectors_table_base(conn_doc, connector=connector, old_base=old_base, new_base=new_base)
    result.files[str(conn_path)] = n
    if n == 0:
        raise RenameError(f"no matching table {old_base!r} found in connector {connector!r} — nothing to rename")

    mapping = {f"{old_base}_{c}": f"{new_base}_{c}" for c in ("get", "put", "post", "delete")}
    _rewrite_cross_file_refs_and_write(docs, result, connector=connector, mapping=mapping)
    return result
