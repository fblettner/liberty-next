"""Apply a packaged ZIP to the current install — the upgrade flow.

Receives a ZIP produced by :func:`liberty.web.package.build_package_zip` (typically built
on a staging install, then shipped to a production install) and merges its contents into
the current ``connectors.toml`` / ``screens.toml`` / ``dictionary.toml`` / etc. per a
strategy:

* ``merge`` — for each entity in the package, KEEP the target's existing version when
  it's present; ADD entities that don't yet exist. Conservative — never overwrites local
  edits. Best for "add new screens / queries to an existing install" flows.

* ``overwrite`` — for each entity in the package, REPLACE the target's version. The
  standard upgrade path: the package is the new truth for the entities it carries; the
  target's other entities (those NOT in the package) are untouched.

* ``replace_all`` — for each file the package touches, REPLACE the entire file with the
  package's version. Drops every entity not in the package. Brutal — only for "deploy
  this install from scratch" flows. Other files (those the package doesn't touch) are
  untouched.

Per-file granularity: the strategy applies independently to each TOML in the ZIP. A
package that only carries `screens.toml` and `dictionary.toml` only touches those files
on the target — `connectors.toml` is left alone regardless of strategy.

Returns a structured summary the UI can render: per-file counts of added / replaced /
skipped entities, plus warnings for missing dependencies (e.g. the package references a
connector the target doesn't have).
"""

from __future__ import annotations

import io
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomli_w
import tomlkit


Strategy = Literal["merge", "overwrite", "replace_all"]


@dataclass(slots=True)
class FileReport:
    """Per-file outcome — what changed when applying the package's `<file>` to the target."""

    file: str
    strategy: str
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # "kept target's" — only used by `merge`
    preserved_overrides: list[str] = field(default_factory=list)
    """Target entities flagged ``override = true`` that ``overwrite`` left alone.
    Surfaces in the import report so the operator can see what their previous edits
    survived (and which ones they may want to clear the flag on to take the new vendor
    version). Empty under ``merge`` (every existing entity is skipped regardless of
    flag) and ``replace_all`` (the whole file is dropped — overrides included)."""
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ImportReport:
    files: list[FileReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reloaded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [
                {"file": f.file, "strategy": f.strategy,
                 "added": list(f.added), "replaced": list(f.replaced),
                 "skipped": list(f.skipped),
                 "preserved_overrides": list(f.preserved_overrides),
                 "errors": list(f.errors)}
                for f in self.files
            ],
            "warnings": list(self.warnings),
            "reloaded": self.reloaded,
        }


def apply_package_zip(
    *,
    zip_bytes: bytes,
    strategy: Strategy,
    settings: Any,
) -> ImportReport:
    """Unzip *zip_bytes*, route each TOML to its target file per the install's *settings*,
    and apply *strategy*. Doesn't reload — caller does that after a successful apply."""
    report = ImportReport()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid ZIP archive: {exc}") from exc

    # Route file → target path on disk. Skip files the package shouldn't carry (app.toml
    # would land secrets; auth / OIDC tokens stay env-only).
    routing: dict[str, Path] = {
        "connectors.toml": Path(settings.connectors.config_path),
        "dictionary.toml": _dictionary_path(settings),
        "screens.toml": Path(settings.screens.config_path),
        "menus.toml": Path(settings.menus.config_path),
        "charts.toml": Path(settings.charts.config_path),
        "dashboards.toml": Path(settings.dashboards.config_path),
    }

    for filename in zf.namelist():
        if filename in ("MANIFEST.md", "manifest.json"):  # informational metadata, not config
            continue
        target = routing.get(filename)
        if target is None:
            report.warnings.append(f"ignored unsupported file in package: {filename}")
            continue
        try:
            pkg_data = tomllib.loads(zf.read(filename).decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            report.files.append(FileReport(file=filename, strategy=strategy, errors=[f"parse failed: {exc}"]))
            continue
        try:
            file_report = _apply_one(filename, pkg_data, target, strategy)
        except Exception as exc:  # noqa: BLE001 — surface to operator with file context
            report.files.append(FileReport(file=filename, strategy=strategy, errors=[f"{type(exc).__name__}: {exc}"]))
            continue
        report.files.append(file_report)

    return report


def _dictionary_path(settings: Any) -> Path:
    """Mirrors liberty.web.admin._dictionary_path — explicit override wins over the
    convention sibling-of-connectors. We need this here so the importer can route
    dictionary.toml without depending on the route module."""
    dict_path = getattr(settings.connectors, "dictionary_path", None)
    if dict_path:
        return Path(dict_path)
    return Path(settings.connectors.config_path).parent / "dictionary.toml"


# ── per-file merge logic ────────────────────────────────────────────────────────────────


def _apply_one(filename: str, pkg_data: dict[str, Any], target: Path, strategy: Strategy) -> FileReport:
    """Merge / overwrite / replace_all one TOML file. Returns a FileReport."""
    report = FileReport(file=filename, strategy=strategy)

    if strategy == "replace_all":
        # Simplest case — the package's file becomes the target file. No diff bookkeeping
        # since we're throwing the target's content out.
        text = tomli_w.dumps(pkg_data)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        # Surface a rough "everything in the package was applied" count so the UI shows something.
        for kind, items in _enumerate_entities(pkg_data, filename):
            for name in items:
                report.replaced.append(f"{kind}: {name}")
        return report

    # merge / overwrite — read existing, walk per-entity, then write back.
    target_data: dict[str, Any] = {}
    if target.exists() and target.read_text(encoding="utf-8").strip():
        try:
            target_data = tomllib.loads(target.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            report.errors.append(f"target {target} is not valid TOML, skipping: {exc}")
            return report

    # The merge algorithm: walk each (kind, name, value) in the package; resolve target's
    # existing value at the same key path; apply per strategy. The OVERRIDE flag adds a
    # short-circuit on ``overwrite``: if the target's existing entity carries
    # ``override = true``, we PRESERVE it (the customer marked it as "don't overwrite on
    # upgrade") and report under preserved_overrides so the operator sees what survived.
    merged = _deep_copy(target_data)
    for kind, items in _enumerate_entities(pkg_data, filename):
        for name, value in items.items():
            existed = _path_exists(merged, kind, name, filename)
            if existed:
                target_value = _get_path(merged, kind, name, filename)
                if strategy == "overwrite" and _is_overridden(target_value):
                    # Customer marked this as a fork — vendor upgrade leaves it alone.
                    report.preserved_overrides.append(f"{kind}: {name}")
                    continue
                if strategy == "merge":
                    report.skipped.append(f"{kind}: {name}")
                    continue
                _set_path(merged, kind, name, value, filename)
                report.replaced.append(f"{kind}: {name}")
            else:
                _set_path(merged, kind, name, value, filename)
                report.added.append(f"{kind}: {name}")

    # menus.toml's [[menus.<app>.items]] is an array, not a dict — handled specially below.
    if filename == "menus.toml":
        _merge_menu_items(merged, pkg_data, strategy, report)

    text = tomli_w.dumps(merged)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return report


def _enumerate_entities(data: dict[str, Any], filename: str):
    """Yield (kind, {name: value}) for each entity kind in *data*. Per-file shapes:
       connectors.toml: pools.<name>, connectors.<name>
       dictionary.toml: entries.<id>, lookups.<id>, sequences.<id>, enums.<id>,
                        connectors.<scope>.entries.<id>, etc.
       screens.toml:    screens.<app>.<id>
       menus.toml:      handled via _merge_menu_items (item arrays)
       charts.toml:     charts.<scope>.<id>
       dashboards.toml: dashboards.<scope>.<id>

    The (kind, name) pairs are FLAT — scope-aware kinds carry the scope in the name as
    "<scope>/<id>" so the merge bookkeeping shows e.g. ``connectors/nomasx1``."""
    if filename == "connectors.toml":
        for n, v in (data.get("pools") or {}).items():
            yield "pool", {n: v}
        for n, v in (data.get("connectors") or {}).items():
            yield "connector", {n: v}
    elif filename == "dictionary.toml":
        for n, v in (data.get("entries") or {}).items():
            yield "entry/shared", {n: v}
        for n, v in (data.get("lookups") or {}).items():
            yield "lookup/shared", {n: v}
        for n, v in (data.get("sequences") or {}).items():
            yield "sequence/shared", {n: v}
        for n, v in (data.get("enums") or {}).items():
            yield "enum/shared", {n: v}
        for scope, section in (data.get("connectors") or {}).items():
            if not isinstance(section, dict):
                continue
            for n, v in (section.get("entries") or {}).items():
                yield f"entry/{scope}", {n: v}
            for n, v in (section.get("lookups") or {}).items():
                yield f"lookup/{scope}", {n: v}
            for n, v in (section.get("sequences") or {}).items():
                yield f"sequence/{scope}", {n: v}
            for n, v in (section.get("enums") or {}).items():
                yield f"enum/{scope}", {n: v}
    elif filename == "screens.toml":
        for app, by_id in (data.get("screens") or {}).items():
            if not isinstance(by_id, dict):
                continue
            for n, v in by_id.items():
                yield f"screen/{app}", {n: v}
    elif filename == "charts.toml":
        for scope, by_id in (data.get("charts") or {}).items():
            if not isinstance(by_id, dict):
                continue
            for n, v in by_id.items():
                yield f"chart/{scope}", {n: v}
    elif filename == "dashboards.toml":
        for scope, by_id in (data.get("dashboards") or {}).items():
            if not isinstance(by_id, dict):
                continue
            for n, v in by_id.items():
                yield f"dashboard/{scope}", {n: v}
    # menus.toml is handled by _merge_menu_items — its [[items]] arrays don't have
    # dict-style ids at the leaf so the generic add/replace path doesn't apply.


def _path_exists(data: dict[str, Any], kind: str, name: str, filename: str) -> bool:
    """Does *data* already carry the entity (kind, name)?"""
    path, leaf = _resolve_path(kind, name, filename)
    cur: Any = data
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return isinstance(cur, dict) and leaf in cur


def _get_path(data: dict[str, Any], kind: str, name: str, filename: str) -> dict[str, Any] | None:
    """Fetch the entity dict at *kind* / *name*. Returns None when not present."""
    path, leaf = _resolve_path(kind, name, filename)
    cur: Any = data
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, dict):
        v = cur.get(leaf)
        return v if isinstance(v, dict) else None
    return None


def _is_overridden(entity: dict[str, Any] | None) -> bool:
    """True when *entity* carries ``override = true`` (a customer-marked fork that
    upgrade-package imports should preserve). Lenient: missing key / None / non-bool
    all read as False so a typo doesn't accidentally lock vendor entities."""
    if not isinstance(entity, dict):
        return False
    return entity.get("override") is True


def _set_path(data: dict[str, Any], kind: str, name: str, value: Any, filename: str) -> None:
    """Insert / replace value at the entity's path inside *data*."""
    path, leaf = _resolve_path(kind, name, filename)
    cur: Any = data
    for k in path:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[leaf] = value


def _resolve_path(kind: str, name: str, filename: str) -> tuple[list[str], str]:
    """Map (kind, name, filename) → (path_keys_to_parent, leaf_key) inside the TOML tree."""
    if filename == "connectors.toml":
        if kind == "pool":      return ["pools"], name
        if kind == "connector": return ["connectors"], name
    if filename == "dictionary.toml":
        # kind is e.g. "entry/shared", "entry/nomasx1", "lookup/nomajde", …
        coll_singular, _, scope = kind.partition("/")
        coll = {"entry": "entries", "lookup": "lookups", "sequence": "sequences", "enum": "enums"}[coll_singular]
        if scope == "shared":
            return [coll], name
        return ["connectors", scope, coll], name
    if filename == "screens.toml":
        _, _, app = kind.partition("/")
        return ["screens", app], name
    if filename == "charts.toml":
        _, _, scope = kind.partition("/")
        return ["charts", scope], name
    if filename == "dashboards.toml":
        _, _, scope = kind.partition("/")
        return ["dashboards", scope], name
    return [], name


def _merge_menu_items(merged: dict[str, Any], pkg_data: dict[str, Any], strategy: Strategy, report: FileReport) -> None:
    """Merge ``[menus.<app>]`` items arrays — keyed by item id within the app's items list."""
    for app, app_menu in (pkg_data.get("menus") or {}).items():
        if not isinstance(app_menu, dict):
            continue
        pkg_items = app_menu.get("items") or []
        if not pkg_items:
            continue
        merged.setdefault("menus", {}).setdefault(app, {})
        target_app = merged["menus"][app]
        if not isinstance(target_app, dict):
            target_app = {}
            merged["menus"][app] = target_app
        target_items = target_app.get("items") or []
        # Index target by id for fast lookup.
        by_id = {it.get("id"): i for i, it in enumerate(target_items) if isinstance(it, dict)}
        for pi in pkg_items:
            iid = pi.get("id") if isinstance(pi, dict) else None
            if iid is None:
                continue
            if iid in by_id:
                target_item = target_items[by_id[iid]]
                if strategy == "overwrite" and _is_overridden(target_item if isinstance(target_item, dict) else None):
                    report.preserved_overrides.append(f"menu_item/{app}: {iid}")
                    continue
                if strategy == "merge":
                    report.skipped.append(f"menu_item/{app}: {iid}")
                    continue
                target_items[by_id[iid]] = pi
                report.replaced.append(f"menu_item/{app}: {iid}")
            else:
                target_items.append(pi)
                by_id[iid] = len(target_items) - 1
                report.added.append(f"menu_item/{app}: {iid}")
        target_app["items"] = target_items


def _deep_copy(data: dict[str, Any]) -> dict[str, Any]:
    """Cheap deep-copy via tomlkit round-trip — TOML data is JSON-shaped (no dates,
    no references, no functions) so a JSON round-trip would also work; tomlkit is just
    avail here already."""
    import json
    return json.loads(json.dumps(data, default=str))


# Re-export tomlkit so the admin route can import it without re-importing.
__all__ = ["Strategy", "ImportReport", "FileReport", "apply_package_zip", "tomlkit"]
