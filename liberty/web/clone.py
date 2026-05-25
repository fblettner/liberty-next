"""Cross-file app cloning — the operation behind ``POST /admin/config/clone-app``.

The setting: an operator wants a parallel deployment of an existing app
(e.g. ``nomasx1`` → ``nomasx1b`` for regression testing — same screens,
same agent jobs, but a separate target database). Five files carry per-app
state and would otherwise need to be edited by hand:

* ``connectors.toml`` — ``[connectors.<app>]`` (the connector itself + its queries)
* ``dictionary.toml`` — ``[connectors.<app>]`` (per-connector dictionary overlay)
* ``menus.toml`` — ``[menus.<app>]``
* ``screens.toml`` — every ``[screens.<app>.*]``
* ``charts.toml`` / ``dashboards.toml`` — any chart/dashboard that pinned to the app

:func:`clone_app` does this **atomically across every affected file** while
preserving comments + formatting via ``tomlkit`` — same shape as
:mod:`liberty.web.rename`, but it *duplicates* the source subtree under a
new name instead of renaming the existing one.

Substitution rules (source name → new name):

* Top-level table key in each file — the only required mapping.
* ``connector = "<source>"`` field values inside the cloned subtree are
  rewritten to ``"<new>"`` so the new app's screens/menus/queries fire
  against the new connector. Other ``connector`` field values pointing
  outside the cloned subtree are left alone (a cross-app reference stays
  pointing where it was).
* ``pool`` field on the cloned connector is set to *new_pool* (the operator
  pre-creates the new pool; we refuse to clone if it doesn't exist).
* All other fields — SQL bodies, query names, screen IDs, menu structure,
  dialog tabs, action chains — are deep-copied verbatim.

What it does NOT do:

* Doesn't create the pool. That's a separate operator action (CREATE
  DATABASE + add ``[pools.<new_pool>]`` to connectors.toml + restart).
* Doesn't initialise the new database's schema. That's
  :func:`nomasx1.db.init_schema` (or the equivalent per-plugin init step).
* Doesn't propagate future changes. After cloning, the two apps diverge —
  edits to the source don't reach the clone.
"""

from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from liberty.connectors.config import parse_connectors
from liberty.connectors.dictionary import parse_dictionary
from liberty.menus.config import parse_menus
from liberty.screens.config import parse_screens


# Same identifier rules as :mod:`liberty.web.rename` — the cloned name must satisfy them
# (it becomes a TOML key, a permission string, a URL segment).
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class CloneError(ValueError):
    """Raised when the clone can't proceed (collision / invalid name / source missing /
    target pool missing)."""


@dataclass
class CloneResult:
    """What :func:`clone_app` actually wrote. Mirrors :class:`RenameResult`'s shape so the
    admin route layer can return either via the same dict."""

    source_app: str
    new_app: str
    new_pool: str
    files: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def total_entries(self) -> int:
        """Total entries cloned across all files (one per cloned top-level table)."""
        return sum(self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_app": self.source_app,
            "new_app": self.new_app,
            "new_pool": self.new_pool,
            "files": self.files,
            "warnings": self.warnings,
            "total_entries": self.total_entries(),
        }


def _validate_identifier(name: str, *, what: str) -> None:
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise CloneError(
            f"invalid {what}: {name!r} — must match {_IDENT_RE.pattern} "
            "(lowercase letters, digits, underscore; leading letter)"
        )


def clone_app(
    source_app: str,
    new_app: str,
    *,
    new_pool: str,
    connectors_path: Path,
    dictionary_path: Path,
    menus_path: Path,
    screens_path: Path,
    dashboards_path: Path | None = None,
    charts_path: Path | None = None,
) -> CloneResult:
    """Clone every per-app entry across the 4 (or 6) config files. See module docstring.

    **Append-only strategy** (the original implementation used tomlkit on every file —
    O(n²) on big nested files: a 150 kB dictionary.toml took 2+ minutes to round-trip,
    same root cause as :func:`put_dictionary_parsed`'s tomli-w switch). Now:

    1. **Pre-flight** — load each file with ``tomllib`` (fast linear parse). Validate
       identifiers, require source to exist in at least one file, require new name to
       NOT exist anywhere, require ``[pools.<new_pool>]`` to exist in connectors.toml.
    2. **In-memory clone** — deep-copy each affected source subtree (pure-dict copies,
       no tomlkit), rewrite ``connector`` field values + ``pool`` field as per the rules.
    3. **Per-file Pydantic validation** — merge the clone INTO the in-memory parsed doc
       and re-validate via the matching ``parse_<kind>``. Catches any cross-reference
       break before the file is touched.
    4. **Append-only write** — generate JUST the new content as a TOML string via
       ``tomli_w.dumps`` and append to the file with a leading blank line. The existing
       content is never re-parsed or re-serialised, so all comments + formatting are
       preserved byte-identically. ~6 ms per file vs the old ~2 min.
    """
    if source_app == new_app:
        raise CloneError(f"source and new names are identical ({source_app!r}) — nothing to clone")
    _validate_identifier(source_app, what="source_app")
    _validate_identifier(new_app, what="new_app")
    _validate_identifier(new_pool, what="new_pool")

    # ── pre-load every affected doc as a plain dict (tomllib is fast even on big files) ──
    docs: dict[str, tuple[Path, dict[str, Any]]] = {}
    for label, path in (
        ("connectors", connectors_path),
        ("dictionary", dictionary_path),
        ("menus", menus_path),
        ("screens", screens_path),
    ):
        if path.exists() and path.read_text(encoding="utf-8").strip():
            with path.open("rb") as fh:
                docs[label] = (path, tomllib.load(fh))
        else:
            docs[label] = (path, {})

    result = CloneResult(source_app=source_app, new_app=new_app, new_pool=new_pool)

    # ── pre-flight 1: target pool must exist ──
    conn_path, conn_doc = docs["connectors"]
    pools = conn_doc.get("pools")
    if not isinstance(pools, dict) or new_pool not in pools:
        raise CloneError(
            f"target pool [{new_pool}] not found in {conn_path}. Create the pool first "
            f"(CREATE DATABASE on Postgres + add [pools.{new_pool}] with the URL + "
            "password via Settings → Pools), then re-run clone-app."
        )

    # ── pre-flight 2: source must exist somewhere ──
    source_locations: list[str] = []
    if _has_top(conn_doc, "connectors", source_app):
        source_locations.append("connectors")
    if _has_top(docs["dictionary"][1], "connectors", source_app):
        source_locations.append("dictionary")
    if _has_top(docs["menus"][1], "menus", source_app):
        source_locations.append("menus")
    if _has_top(docs["screens"][1], "screens", source_app):
        source_locations.append("screens")
    if not source_locations:
        raise CloneError(
            f"source app {source_app!r} has no entries in any of {sorted(docs)!r} "
            "— check the name (typo? wrong app?)."
        )

    # ── pre-flight 3: new name must NOT exist anywhere ──
    for label, top_key in (("connectors", "connectors"), ("dictionary", "connectors"),
                            ("menus", "menus"), ("screens", "screens")):
        _, doc = docs[label]
        if _has_top(doc, top_key, new_app):
            raise CloneError(
                f"clone would clash with existing [{top_key}.{new_app}] in "
                f"{docs[label][0]}. Delete the existing entry first or pick a different name."
            )

    # ── build each new subtree in memory + validate by merging into the parsed doc ──
    # Each entry is (path, top_key, new_app, cloned_subtree). The validation merges the
    # clone into the parsed doc + re-runs the Pydantic parser; the append write only
    # emits ``{top_key: {new_app: cloned}}`` so existing content is byte-identical.
    new_subtrees: list[tuple[str, Path, str, str, dict[str, Any]]] = []

    if "connectors" in source_locations:
        cloned = copy.deepcopy(conn_doc["connectors"][source_app])
        if isinstance(cloned, dict) and "pool" in cloned:
            cloned["pool"] = new_pool
        _replace_connector_field_recursive(cloned, source=source_app, new=new_app)
        new_subtrees.append(("connectors", conn_path, "connectors", new_app, cloned))
        result.files[str(conn_path)] = 1
    else:
        result.files[str(conn_path)] = 0
        result.warnings.append(
            f"no [connectors.{source_app}] in {conn_path} — cloned the other files anyway, "
            "but the new app has no connector definition until you add one manually."
        )

    dict_path, dict_doc = docs["dictionary"]
    if "dictionary" in source_locations:
        cloned = copy.deepcopy(dict_doc["connectors"][source_app])
        _replace_connector_field_recursive(cloned, source=source_app, new=new_app)
        new_subtrees.append(("dictionary", dict_path, "connectors", new_app, cloned))
        result.files[str(dict_path)] = 1
    else:
        result.files[str(dict_path)] = 0

    menus_path, menus_doc = docs["menus"]
    if "menus" in source_locations:
        cloned = copy.deepcopy(menus_doc["menus"][source_app])
        _replace_connector_field_recursive(cloned, source=source_app, new=new_app)
        new_subtrees.append(("menus", menus_path, "menus", new_app, cloned))
        result.files[str(menus_path)] = 1
    else:
        result.files[str(menus_path)] = 0

    screens_path, screens_doc = docs["screens"]
    if "screens" in source_locations:
        cloned = copy.deepcopy(screens_doc["screens"][source_app])
        _replace_connector_field_recursive(cloned, source=source_app, new=new_app)
        new_subtrees.append(("screens", screens_path, "screens", new_app, cloned))
        result.files[str(screens_path)] = 1
    else:
        result.files[str(screens_path)] = 0

    if dashboards_path is not None or charts_path is not None:
        result.warnings.append(
            "dashboards.toml / charts.toml not auto-cloned — saved charts and dashboards "
            f"that pinned to {source_app!r} need to be duplicated manually if you want them "
            "available on the new app too."
        )

    # ── per-file Pydantic validation against the merged dict ──
    parsers = {
        "connectors": parse_connectors,
        "dictionary": parse_dictionary,
        "menus": parse_menus,
        "screens": parse_screens,
    }
    for label, path, top_key, name, cloned in new_subtrees:
        merged = copy.deepcopy(docs[label][1])
        merged.setdefault(top_key, {})[name] = cloned
        try:
            parsers[label](merged)
        except Exception as exc:                 # noqa: BLE001
            raise CloneError(
                f"clone would make {label} ({path}) invalid: {exc}"
            ) from exc

    # ── append-only write ──
    # Build a tiny dict per file containing only ``{top_key: {new_name: cloned}}``,
    # serialise with tomli_w (linear in the cloned subtree's size — irrelevant of the
    # existing file's size), append. Existing bytes stay untouched.
    for label, path, top_key, name, cloned in new_subtrees:
        chunk = tomli_w.dumps({top_key: {name: cloned}})
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_bytes() if path.exists() else b""
        sep = b"\n\n" if existing and not existing.endswith(b"\n\n") else b""
        with path.open("ab") as fh:
            fh.write(sep)
            fh.write(chunk.encode("utf-8"))

    return result


# ── per-file cloners ─────────────────────────────────────────────────────────────────────


def _has_top(doc: dict[str, Any], top_key: str, name: str) -> bool:
    """True if doc[top_key][name] exists."""
    table = doc.get(top_key)
    return isinstance(table, dict) and name in table


@dataclass
class DeleteAppResult:
    """What :func:`delete_app` actually removed. Mirrors :class:`CloneResult` so the admin
    route layer can return either via the same dict."""

    app: str
    files: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def total_sections(self) -> int:
        """Total section blocks deleted across all files."""
        return sum(self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "files": self.files,
            "warnings": self.warnings,
            "total_sections": self.total_sections(),
        }


def delete_app(
    app: str,
    *,
    connectors_path: Path,
    dictionary_path: Path,
    menus_path: Path,
    screens_path: Path,
) -> DeleteAppResult:
    """Remove every per-app entry across the 4 config files. The cross-file inverse of
    :func:`clone_app` — same files touched, same name semantics, opposite direction.

    **Surgical text-edit strategy** — same reason :func:`clone_app` is append-only: tomlkit
    is O(n²) on big nested files (a 150 kB dictionary.toml takes minutes to round-trip).
    Instead:

    1. Read each file as text (fast).
    2. Walk lines, find every section header that matches ``[<top_key>.<app>...]`` or
       ``[[<top_key>.<app>...]]``, excise those blocks (the lines belonging to the
       section, NOT trailing blank/comment lines that introduce the next section).
    3. Validate the result via ``tomllib.loads`` + the matching Pydantic parser
       (catches a mis-bounded excise before we touch the file).
    4. Write back. Comments + formatting on every untouched section survive byte-identical.

    Refuses if the app doesn't exist in any of the 4 files (typo guard — the operator
    expected SOMETHING to disappear; surfacing "nothing found" is the right answer).
    Pool deletion is NOT included — pools are managed separately (Settings → Pools);
    deleting an app shouldn't auto-drop a pool that other apps might still use.
    """
    _validate_identifier(app, what="app")
    result = DeleteAppResult(app=app)

    targets: list[tuple[str, Path, str]] = [
        ("connectors", connectors_path, "connectors"),  # [connectors.<app>]
        ("dictionary", dictionary_path, "connectors"),  # [connectors.<app>] in dictionary
        ("menus", menus_path, "menus"),                  # [menus.<app>]
        ("screens", screens_path, "screens"),            # [screens.<app>.*]
    ]

    parsers = {
        "connectors": parse_connectors,
        "dictionary": parse_dictionary,
        "menus": parse_menus,
        "screens": parse_screens,
    }

    pending_writes: list[tuple[Path, str]] = []
    for label, path, top_key in targets:
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            result.files[str(path)] = 0
            continue
        src = path.read_text(encoding="utf-8")
        new_text, n = _excise_sections(src, top_key=top_key, name=app)
        result.files[str(path)] = n
        if n == 0:
            continue
        # Validate the rewritten text — both that it's still valid TOML and that the
        # Pydantic schema accepts it (catches any cross-reference that pointed at the
        # deleted app and now dangles).
        try:
            parsers[label](tomllib.loads(new_text))
        except Exception as exc:                 # noqa: BLE001
            raise CloneError(
                f"delete would make {label} ({path}) invalid: {exc}"
            ) from exc
        pending_writes.append((path, new_text))

    if result.total_sections() == 0:
        raise CloneError(
            f"app {app!r} has no entries in any of "
            f"{[str(p) for _, p, _ in targets]} — nothing to delete (typo?)."
        )

    # All validations passed — write every touched file in one batch.
    for path, new_text in pending_writes:
        path.write_text(new_text, encoding="utf-8")

    return result


def _excise_sections(text: str, *, top_key: str, name: str) -> tuple[str, int]:
    """Walk *text* line-by-line, remove every TOML section block whose header starts
    with ``[<top_key>.<name>`` or ``[[<top_key>.<name>``. Return ``(new_text, count)``.

    "Belonging to the section" = lines between the section header and the next sibling
    section header (a different top-level table). Trailing blank lines + comments that
    introduce the NEXT section are preserved — they belong to that section visually, not
    to the one being deleted.
    """
    import re

    # Our section header: opens with [ or [[, then top_key.name, then either ] / ]] /
    # .more.path. Leading whitespace allowed (tomlkit-saved files have none, but
    # hand-edited ones may indent).
    ours = re.compile(
        rf"^\s*\[\[?\s*{re.escape(top_key)}\.{re.escape(name)}(?:\.[^\]\s]+)*\s*\]\]?\s*(?:#.*)?$"
    )
    # Any section header at all — for detecting "we've left our section".
    any_header = re.compile(r"^\s*\[\[?\s*[A-Za-z_]")

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    # Trailing buffer: blank lines + comments that COULD belong to our section's tail
    # OR could be the preface to the NEXT section. We can't tell until we see what
    # follows. If a sibling header comes next → flush the buffer (those are preface).
    # If our section continues → drop the buffer.
    tail_buf: list[str] = []
    excise = False
    n_removed = 0

    for line in lines:
        stripped = line.strip()
        if ours.match(stripped):
            # Pop preceding comment lines from `out` — convention is that a comment
            # block immediately above a section describes/introduces that section, so
            # it should die with the section. Stop at the first non-comment line
            # (blank lines stay as separators between previous content + the next
            # section we're about to introduce).
            while out and out[-1].strip().startswith("#"):
                out.pop()
            # Start (or continue) excising; reset the tail buffer because we just
            # crossed a real section boundary that confirms the buffered lines were
            # in our section, not preface for someone else.
            if not excise:
                n_removed += 1
            excise = True
            tail_buf = []
            continue
        if excise and any_header.match(stripped):
            # Hit a sibling top-level section header — stop excising. Flush the tail
            # buffer (those blanks/comments preface this sibling section).
            out.extend(tail_buf)
            tail_buf = []
            out.append(line)
            excise = False
            continue
        if excise:
            if stripped == "" or stripped.startswith("#"):
                # Could be tail of our section, could be preface to the next — buffer.
                tail_buf.append(line)
            else:
                # Real content inside our section — drop the buffer + this line.
                tail_buf = []
            continue
        out.append(line)

    # EOF reached while excising → the buffered blanks/comments were trailing whitespace
    # at the end of the file; drop them. (If we'd just hit a sibling header, the buffer
    # would have been flushed above.)
    return "".join(out), n_removed


async def clone_app_step(
    *,
    settings: Any,                       # liberty.config.Settings (typed loosely to avoid the import)
    source_app: str,
    new_app: str,
    new_pool: str,
) -> dict[str, Any]:
    """Python-step wrapper around :func:`clone_app` — pulls the four config paths from
    *settings* and delegates. Used as a step callable in jobs.toml (e.g. the
    ``nomasx1-init-db`` job clones the app then runs the schema init).

    Returns the :class:`CloneResult` serialised to a dict — the PythonStepExecutor wraps
    that into a ``StepResult(extras=…)`` so the operator sees the per-file counts +
    warnings in the run's step row.
    """
    # Local imports — avoid a hard module-load dependency on liberty.config (which
    # imports a lot) since this wrapper is only ever called from a python step that
    # already has Settings available.
    from pathlib import Path  # noqa: WPS433 — local import keeps the module dep-free at top
    result = clone_app(
        source_app, new_app, new_pool=new_pool,
        connectors_path=Path(settings.connectors.config_path),
        dictionary_path=Path(settings.connectors.dictionary_path) if settings.connectors.dictionary_path else
                        Path(settings.connectors.config_path).parent / "dictionary.toml",
        menus_path=Path(settings.menus.config_path),
        screens_path=Path(settings.screens.config_path),
    )
    return result.to_dict()


def _replace_connector_field_recursive(node: Any, *, source: str, new: str) -> int:
    """Walk a tomlkit / dict / list tree and rewrite ``connector = "<source>"`` (and
    ``row_click_connector``) wherever it appears. Returns the count of touched fields.
    Same field list as :mod:`liberty.web.rename` — listing them explicitly so an unrelated
    field that happens to carry the source name (a column name, an enum value) doesn't
    accidentally change."""
    n = 0
    if isinstance(node, dict):
        for key in ("connector", "row_click_connector"):
            if node.get(key) == source:
                node[key] = new
                n += 1
        for v in node.values():
            n += _replace_connector_field_recursive(v, source=source, new=new)
    elif isinstance(node, list):
        for v in node:
            n += _replace_connector_field_recursive(v, source=source, new=new)
    return n
