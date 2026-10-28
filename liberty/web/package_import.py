"""Apply — or preview — a packaged ZIP on this install: the import side of Package & Deploy and
of a promotion from another instance.

Receives a ZIP produced by :func:`liberty.web.package.build_package_zip` and merges its TOML
files into this install's config, **entity by entity**, per a strategy:

* ``merge`` — an entity this install already has is KEPT (reported ``skipped`` when it differs);
  new ones are added. Never overwrites local edits.
* ``overwrite`` — the package's version REPLACES this install's for the entities it carries
  (the standard promotion); an entity flagged ``override = true`` here is kept (``kept``). The
  install's other entities are untouched.
* ``replace_all`` — each file the package carries REPLACES this install's file whole; entities
  missing from the package are dropped (reported ``removed``). Only for "deploy from scratch".

Granularity — what one "entity" is, per file:

* ``connectors.toml`` — a pool; a connector's header (every field but its item lists); each
  query / table / sequence / lookup / endpoint of a connector, by name. A package carrying only a
  connector's items (the connector itself not selected) merges those items into this install's
  connector — and skips them when the connector doesn't exist here.
* ``menus.toml`` — each menu item, by id within its app.
* ``jobs.toml`` — each job, by id. A job this install already has keeps its own schedule,
  enabled flag, timezone, presets, alerts, retry, log level and timeout (the instance's choices);
  a new job arrives **disabled**.
* the others (dictionary, screens, charts, dashboards, actions, explorer, ai, notifications) — the
  dict-keyed entities :func:`liberty.config_merge.enumerate_entities` lists.

``dry_run`` computes the same plan without writing a byte. The plan lists every entity with its
state — ``new`` / ``changed`` (with a field-by-field diff) / ``identical`` / ``kept`` / ``skipped``
/ ``removed`` — plus warnings: the package comes from a newer framework, an ``ENC:`` value can't
be decrypted with this install's master key.

``connectors.toml`` and ``jobs.toml`` are edited through tomlkit (their comments survive); the
big builder-owned files through tomllib + tomli_w (see CLAUDE.md, the TOML rule).
"""

from __future__ import annotations

import io
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomli_w
import tomlkit

from liberty.config_merge import JOB_OPERATOR_FIELDS, enumerate_entities, get_path, is_overridden, set_path

Strategy = Literal["merge", "overwrite", "replace_all"]

# A connector's item lists, keyed by ``name`` — merged one item at a time.
CONNECTOR_ITEMS: dict[str, str] = {
    "queries": "query", "tables": "table", "sequences": "sequence", "lookups": "lookup", "endpoints": "endpoint",
}
_ITEM_ARRAY = {singular: arr for arr, singular in CONNECTOR_ITEMS.items()}
TOMLKIT_FILES = frozenset({"connectors.toml", "jobs.toml"})

# A package unpacks to TOML text — a few MB at the very most. Anything declaring more
# is malformed or hostile (a zip bomb expands to GBs from a few KB); refuse before reading
# a single member.
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
_DIFF_CAP = 200          # field changes listed per entity
_VALUE_CAP = 240         # characters of one value shown in a diff
_SECRET_KEY = re.compile(r"(password|secret|token|api_?key|client_secret)$", re.IGNORECASE)


@dataclass(slots=True)
class FileReport:
    """Per-file outcome: the plan of its entities, and the legacy per-state name lists."""

    file: str
    strategy: str
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # "kept this install's" — ``merge``, or no connector here
    preserved_overrides: list[str] = field(default_factory=list)
    """Entities flagged ``override = true`` here that ``overwrite`` left alone."""
    errors: list[str] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    """``{kind, name, state, detail?, diff?}`` per entity — the plan the UI renders."""


@dataclass(slots=True)
class ImportReport:
    files: list[FileReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reloaded: bool = False
    dry_run: bool = False
    source: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        out = {s: 0 for s in ("new", "changed", "identical", "kept", "skipped", "removed")}
        for f in self.files:
            for e in f.entities:
                out[e["state"]] = out.get(e["state"], 0) + 1
        out["errors"] = sum(len(f.errors) for f in self.files)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [
                {"file": f.file, "strategy": f.strategy,
                 "added": list(f.added), "replaced": list(f.replaced),
                 "skipped": list(f.skipped),
                 "preserved_overrides": list(f.preserved_overrides),
                 "errors": list(f.errors), "entities": list(f.entities)}
                for f in self.files
            ],
            "warnings": list(self.warnings),
            "reloaded": self.reloaded,
            "dry_run": self.dry_run,
            "source": dict(self.source),
            "summary": self.summary(),
        }


# ── where each packaged file lands ──────────────────────────────────────────────────────


def _dictionary_path(settings: Any) -> Path:
    """Mirrors liberty.web.admin._dictionary_path — explicit override wins over the
    convention sibling-of-connectors."""
    dict_path = getattr(settings.connectors, "dictionary_path", None)
    if dict_path:
        return Path(dict_path)
    return Path(settings.connectors.config_path).parent / "dictionary.toml"


def package_files(settings: Any) -> dict[str, Path]:
    """The config file each packaged TOML maps to on this install (app.toml / auth.toml / theme are
    never packaged: secrets, accounts and branding stay per instance)."""
    out: dict[str, Path] = {
        "connectors.toml": Path(settings.connectors.config_path),
        "dictionary.toml": _dictionary_path(settings),
        "screens.toml": Path(settings.screens.config_path),
        "menus.toml": Path(settings.menus.config_path),
        "charts.toml": Path(settings.charts.config_path),
        "dashboards.toml": Path(settings.dashboards.config_path),
        "actions.toml": Path(settings.actions.config_path),
        "explorer.toml": Path(settings.explorer.config_path),
        "notifications.toml": Path(settings.notify.config_path),
        "jobs.toml": Path(settings.jobs.config_path),
    }
    ai_path = getattr(settings.ai, "config_path", None)
    if ai_path:
        out["ai.toml"] = Path(ai_path)
    return out


# ── the import ──────────────────────────────────────────────────────────────────────────


def apply_package_zip(
    *,
    zip_bytes: bytes,
    strategy: Strategy,
    settings: Any,
    dry_run: bool = False,
    version: str | None = None,
) -> ImportReport:
    """Unzip *zip_bytes*, plan each TOML against its file on this install per *strategy*, and —
    unless *dry_run* — write the result. *version* is this install's framework version (a warning
    when the package comes from a newer one). Doesn't reload — the caller does after an apply."""
    report = ImportReport(dry_run=dry_run)
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid ZIP archive: {exc}") from exc
    unpacked = sum(info.file_size for info in zf.infolist())
    if unpacked > MAX_UNPACKED_BYTES:
        raise ValueError(
            f"package declares {unpacked // (1024 * 1024)} MB unpacked — above the "
            f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB limit; not a deployment package"
        )

    if "manifest.json" in zf.namelist():
        try:
            report.source = dict((json.loads(zf.read("manifest.json")) or {}).get("source") or {})
        except (ValueError, UnicodeDecodeError):
            report.source = {}
    src_version = report.source.get("version")
    if version and src_version and _version_key(src_version) > _version_key(version):
        report.warnings.append(
            f"the package comes from Liberty Next {src_version}; this instance runs {version} — "
            "settings it doesn't know yet may be rejected at reload: upgrade this instance first")

    routing = package_files(settings)
    master_key = getattr(getattr(settings, "crypto", None), "master_key", "") or ""
    undecryptable: list[str] = []
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
        _check_encrypted(pkg_data, master_key, filename, undecryptable)
        try:
            file_report = _apply_one(filename, pkg_data, target, strategy, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 — surface to operator with file context
            report.files.append(FileReport(file=filename, strategy=strategy, errors=[f"{type(exc).__name__}: {exc}"]))
            continue
        report.files.append(file_report)

    if undecryptable:
        more = f" (and {len(undecryptable) - 5} more)" if len(undecryptable) > 5 else ""
        report.warnings.append(
            "encrypted values this instance can't decrypt (another master key) — set them again here after "
            f"the import: {', '.join(undecryptable[:5])}{more}")
    return report


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:4])


def _check_encrypted(node: Any, master_key: str, where: str, out: list[str]) -> None:
    """Collect the places of the ``ENC:`` values this install's *master_key* can't decrypt."""
    from liberty.crypto import decrypt
    if isinstance(node, dict):
        for k, v in node.items():
            _check_encrypted(v, master_key, f"{where}:{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            label = v.get("name") or v.get("id") if isinstance(v, dict) else None
            _check_encrypted(v, master_key, f"{where}[{label if label else i}]", out)
    elif isinstance(node, str) and node.startswith("ENC:"):
        try:
            if not master_key:
                raise ValueError("no master key")
            decrypt(node, master_key)
        except Exception:  # noqa: BLE001 — any failure means "not usable here"
            out.append(where)


# ── one file ────────────────────────────────────────────────────────────────────────────


def _units(data: Any, filename: str) -> list[tuple[str, str, Any]]:
    """Every entity of *data* as ``(kind, name, value)`` — the plan's unit (see the module doc)."""
    out: list[tuple[str, str, Any]] = []
    if filename == "connectors.toml":
        for n, v in (data.get("pools") or {}).items():
            out.append(("pool", str(n), v))
        for cname, conn in (data.get("connectors") or {}).items():
            if not isinstance(conn, dict):
                continue
            header = _header(conn)
            if header:
                out.append(("connector", str(cname), header))
            for arr, singular in CONNECTOR_ITEMS.items():
                for it in conn.get(arr) or []:
                    if isinstance(it, dict) and it.get("name"):
                        out.append((f"{singular}/{cname}", str(it["name"]), it))
        return out
    if filename == "jobs.toml":
        for j in data.get("jobs") or []:
            if isinstance(j, dict) and j.get("id"):
                out.append(("job", str(j["id"]), j))
        return out
    for kind, items in enumerate_entities(data, filename):
        for name, value in items.items():
            if kind == "menu_app" and not value:
                continue   # a package's menus carry items only — no header to merge
            out.append((kind, str(name), value))
    return out


def _header(conn: Any) -> dict[str, Any]:
    return {k: v for k, v in conn.items() if k not in CONNECTOR_ITEMS} if isinstance(conn, dict) else {}


def _get(doc: Any, filename: str, kind: str, name: str) -> Any:
    fam, _, scope = kind.partition("/")
    if filename == "connectors.toml":
        if kind == "pool":
            return (doc.get("pools") or {}).get(name)
        conn = (doc.get("connectors") or {}).get(name if kind == "connector" else scope)
        if conn is None:
            return None
        if kind == "connector":
            return _header(conn)
        return next((it for it in conn.get(_ITEM_ARRAY[fam]) or [] if it.get("name") == name), None)
    if filename == "jobs.toml":
        return next((j for j in doc.get("jobs") or [] if j.get("id") == name), None)
    if fam == "menu_item":
        app_menu = (doc.get("menus") or {}).get(scope) or {}
        return next((it for it in app_menu.get("items") or [] if str(it.get("id")) == name), None)
    if kind == "menu_app":   # the header: the app's menu without its items
        app_menu = (doc.get("menus") or {}).get(name)
        return {k: v for k, v in app_menu.items() if k != "items"} if isinstance(app_menu, dict) else None
    return get_path(doc, kind, name, filename)


def _set(doc: Any, filename: str, kind: str, name: str, value: Any) -> None:
    fam, _, scope = kind.partition("/")
    if filename == "connectors.toml":
        if kind == "pool":
            if "pools" not in doc:
                doc["pools"] = tomlkit.table(is_super_table=True)
            doc["pools"][name] = _tk(value)
            return
        if "connectors" not in doc:
            doc["connectors"] = tomlkit.table(is_super_table=True)
        conns = doc["connectors"]
        cname = name if kind == "connector" else scope
        if cname not in conns:
            conns[cname] = tomlkit.table()
        conn = conns[cname]
        if kind == "connector":
            for k in [k for k in conn if k not in CONNECTOR_ITEMS and k not in value]:
                del conn[k]
            for k, v in value.items():
                conn[k] = _tk(v)
            return
        _put_item(conn, _ITEM_ARRAY[fam], "name", name, value, tomlkit_doc=True)
        return
    if filename == "jobs.toml":
        _put_item(doc, "jobs", "id", name, value, tomlkit_doc=True)
        return
    if fam == "menu_item":
        app_menu = doc.setdefault("menus", {}).setdefault(scope, {})
        _put_item(app_menu, "items", "id", name, value, tomlkit_doc=False)
        return
    set_path(doc, kind, name, value, filename)


def _put_item(parent: Any, arr: str, key: str, name: str, value: Any, *, tomlkit_doc: bool) -> None:
    """Replace the item of ``parent[arr]`` whose *key* is *name*, or append it."""
    if arr not in parent:
        parent[arr] = tomlkit.aot() if tomlkit_doc else []
    items = parent[arr]
    new = _tk(value) if tomlkit_doc else value
    for i, it in enumerate(items):
        if str(it.get(key)) == name:
            items[i] = new
            return
    items.append(new)


def _tk(value: Any) -> Any:
    """A value for a tomlkit document: SQL and other multi-line strings stay multi-line."""
    if isinstance(value, str) and "\n" in value:
        return tomlkit.string(value, multiline=True)
    if isinstance(value, dict):
        return {k: _tk(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_tk(v) for v in value]
    return value


def _plain(value: Any) -> Any:
    """A tomlkit item as plain Python (no-op for tomllib data)."""
    if hasattr(value, "unwrap"):
        return value.unwrap()
    return value


def _same(a: Any, b: Any) -> bool:
    return json.dumps(_plain(a), sort_keys=True, default=str) == json.dumps(_plain(b), sort_keys=True, default=str)


def _job_for_target(pkg_job: dict[str, Any], target_job: dict[str, Any] | None) -> dict[str, Any]:
    """The package's job as it lands here: this install's schedule / enabled / … kept; a new job
    disabled (it shouldn't start running on the target before someone looks at it)."""
    if target_job is None:
        return {**pkg_job, "enabled": False}
    target_job = _plain(target_job)
    out = {k: v for k, v in pkg_job.items() if k not in JOB_OPERATOR_FIELDS}
    out.update({k: target_job[k] for k in JOB_OPERATOR_FIELDS if k in target_job})
    return out


def _load(filename: str, target: Path) -> Any:
    if target.exists() and target.read_text(encoding="utf-8").strip():
        text = target.read_text(encoding="utf-8")
        return tomlkit.parse(text) if filename in TOMLKIT_FILES else tomllib.loads(text)
    return tomlkit.document() if filename in TOMLKIT_FILES else {}


def _dump(filename: str, doc: Any) -> str:
    return tomlkit.dumps(doc) if filename in TOMLKIT_FILES else tomli_w.dumps(doc, multiline_strings=True)


def _apply_one(filename: str, pkg_data: dict[str, Any], target: Path, strategy: Strategy, *, dry_run: bool = False) -> FileReport:
    """Plan one TOML file against *target* and — unless *dry_run* — write the result."""
    report = FileReport(file=filename, strategy=strategy)
    try:
        doc = _load(filename, target)
    except (tomllib.TOMLDecodeError, tomlkit.exceptions.ParseError) as exc:
        report.errors.append(f"target {target} is not valid TOML, skipping: {exc}")
        return report

    pkg_units = _units(pkg_data, filename)
    pkg_conns = {n for k, n, _ in pkg_units if k == "connector"}
    writes = 0
    for kind, name, value in pkg_units:
        label = f"{kind}: {name}"
        current = _get(doc, filename, kind, name)
        incoming = _job_for_target(value, current) if filename == "jobs.toml" else value
        entity: dict[str, Any] = {"kind": kind, "name": name}
        fam, _, scope = kind.partition("/")
        if filename == "connectors.toml" and fam in _ITEM_ARRAY and scope not in pkg_conns \
                and (doc.get("connectors") or {}).get(scope) is None:
            entity.update(state="skipped", detail=f"connector {scope} doesn't exist here — include the connector in the package")
            report.skipped.append(label)
        elif current is None:
            entity["state"] = "new"
            if filename == "jobs.toml":
                entity["detail"] = "arrives disabled"
            report.added.append(label)
            if strategy != "replace_all":
                _set(doc, filename, kind, name, incoming)
                writes += 1
        elif strategy != "replace_all" and is_overridden(_plain(current)):
            entity.update(state="kept", detail="override = true here")
            report.preserved_overrides.append(label)
        elif _same(current, incoming):
            entity["state"] = "identical"
        elif strategy == "merge":
            entity.update(state="skipped", detail="exists here (merge keeps it)", diff=field_diff(_plain(current), incoming))
            report.skipped.append(label)
        else:
            entity.update(state="changed", diff=field_diff(_plain(current), incoming))
            if filename == "jobs.toml":
                entity["detail"] = "schedule, enabled and alerts kept from this instance"
            report.replaced.append(label)
            if strategy != "replace_all":
                _set(doc, filename, kind, name, incoming)
                writes += 1
        report.entities.append(entity)

    if strategy == "replace_all":
        keys = {(k, n) for k, n, _ in pkg_units}
        for kind, name, _ in _units(_plain(doc), filename):
            if (kind, name) not in keys:
                report.entities.append({"kind": kind, "name": name, "state": "removed"})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tomli_w.dumps(pkg_data, multiline_strings=True), encoding="utf-8")
        return report

    if writes and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump(filename, doc), encoding="utf-8")
    return report


# ── field diff ──────────────────────────────────────────────────────────────────────────


def field_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """The changed leaves between two entity values: ``[{key, from, to}]`` (``from`` / ``to`` null
    when absent). Lists of tables are matched by their ``id`` / ``name`` (``columns[USR_ID].label``),
    other lists compared whole. Secrets are masked. Capped at :data:`_DIFF_CAP` entries."""
    out: list[dict[str, Any]] = []
    _diff(old, new, path, out)
    return out[:_DIFF_CAP]


def _keyed(items: Any) -> dict[str, Any] | None:
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        return None
    for k in ("id", "name"):
        if all(i.get(k) is not None for i in items):
            keys = [str(i[k]) for i in items]
            if len(set(keys)) == len(keys):
                return dict(zip(keys, items, strict=True))
    return None


def _diff(old: Any, new: Any, path: str, out: list[dict[str, Any]]) -> None:
    if len(out) >= _DIFF_CAP or _same(old, new):
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for k in [*new.keys(), *[k for k in old if k not in new]]:
            _diff(old.get(k), new.get(k), f"{path}.{k}" if path else str(k), out)
        return
    ko, kn = _keyed(old), _keyed(new)
    if ko is not None and kn is not None:
        for k in [*kn, *[k for k in ko if k not in kn]]:
            _diff(ko.get(k), kn.get(k), f"{path}[{k}]", out)
        return
    out.append({"key": path, "from": _fmt(old, path), "to": _fmt(new, path)})


def _fmt(value: Any, path: str) -> str | None:
    if value is None:
        return None
    leaf = path.rsplit(".", 1)[-1]
    if (isinstance(value, str) and value.startswith("ENC:")) or (_SECRET_KEY.search(leaf) and isinstance(value, str) and not value.startswith("${")):
        return "••••"
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= _VALUE_CAP else text[:_VALUE_CAP] + "…"


# Re-export tomlkit so the admin route can import it without re-importing.
__all__ = ["Strategy", "ImportReport", "FileReport", "apply_package_zip", "package_files", "field_diff", "tomlkit"]
