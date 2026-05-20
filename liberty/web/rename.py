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

Out of scope here (delete + re-add for now, or a later slice):

* Renaming a *screen app* (``[screens.<app>]``) — distinct from the connector even though they
  conventionally share a name. The matching ``[menus.<app>]`` and any cross-references would
  need a separate operation.
* Renaming a *sequence id* / *lookup id* / *dictionary entry key* — narrower scope (just
  ``dictionary.toml`` + the queries that ``#SEQUENCE.<id>#`` / ``LOOKUP.<id>`` in their SQL),
  but the same shape would apply.
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
    # write anything for it). The list below pairs each path with the in-memory ``tomlkit``
    # document we'll rewrite and the Pydantic validator that has to accept the result.
    docs: dict[str, tuple[Path, tomlkit.TOMLDocument]] = {}
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
        if path.exists() and path.read_text(encoding="utf-8").strip():
            docs[label] = (path, tomlkit.parse(path.read_text(encoding="utf-8")))
        else:
            docs[label] = (path, tomlkit.document())
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
    for label, (path, doc) in docs.items():
        if not result.files.get(str(path), 0):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    return result


def _validate(label: str, doc: tomlkit.TOMLDocument, parser: Any, path: Path) -> None:
    """Re-parse the rewritten document via ``tomllib`` (tomlkit's structure is a superset, and
    Pydantic validators expect plain dicts) and call the matching Pydantic parser. Any error
    becomes a :class:`RenameError` referencing the file + the underlying message."""
    try:
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
    return _replace_connector_field_recursive(doc.get("menus"), old=old, new=new)


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
    """Walk every widget on every dashboard + every DashboardFilterOptions and update
    ``connector = "<old>"`` field values. Widgets carry it directly; KpiWidget requires it;
    ChartWidget's is nullable but always set in inline mode."""
    return _replace_connector_field_recursive(doc.get("dashboards"), old=old, new=new)


def _rewrite_charts_doc(doc: tomlkit.TOMLDocument, *, old: str, new: str) -> int:
    return _replace_connector_field_recursive(doc.get("charts"), old=old, new=new)


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
