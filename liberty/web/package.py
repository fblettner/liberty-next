"""Build a deployable ZIP package from a dependency manifest.

Given the closure produced by :mod:`liberty.web.dependencies`, assemble a self-contained
``config/`` slice the operator can rsync to the target install and reload. The package
includes only what the seeds depend on — connectors are trimmed to their referenced
queries, menus include only the items the screens reach, etc.

Secrets are NOT stripped — ``ENC:...`` and ``${VAR}`` references pass through verbatim
so a target install with the same master_key / env vars round-trips cleanly. The
``MANIFEST.md`` lists the env vars the operator must set on the target.

With *settings*, every entity is taken from this install's TOML files as written (sparse, the
``${VAR}`` references unresolved) — so the importer's diff against the target's own file shows
real changes, not every default value; the dependency's model dump is the fallback (a preset-
derived Explorer model, a screen version bundle built without settings). A connector's queries
ship even when the connector itself isn't selected: the importer merges them into the target's
connector item by item.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from typing import Any

import tomli_w

from liberty.config_merge import get_path
from liberty.web.dependencies import Dependency, Manifest
from liberty.web.package_import import CONNECTOR_ITEMS, package_files


class _Source:
    """The source install's config files, read once each — where an entity is taken from."""

    def __init__(self, settings: Any | None) -> None:
        self.paths = package_files(settings) if settings is not None else {}
        self._docs: dict[str, dict[str, Any]] = {}

    def doc(self, filename: str) -> dict[str, Any]:
        if filename not in self._docs:
            import tomllib
            path = self.paths.get(filename)
            try:
                self._docs[filename] = tomllib.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
            except (tomllib.TOMLDecodeError, OSError):
                self._docs[filename] = {}
        return self._docs[filename]

    def connector(self, name: str) -> dict[str, Any] | None:
        raw = (self.doc("connectors.toml").get("connectors") or {}).get(name)
        return raw if isinstance(raw, dict) else None

    def value(self, d: Dependency) -> Any:
        """*d* as written in its file, else its model dump."""
        raw: Any = None
        if self.paths:
            k, scope = d.kind, d.scope or "shared"
            if k in ("screen", "chart", "dashboard"):
                f = f"{k}s.toml"
                raw = get_path(self.doc(f), f"{k}/{d.scope}", d.name, f)
            elif k in ("dictionary_entry", "lookup", "sequence", "enum"):
                singular = "entry" if k == "dictionary_entry" else k
                raw = get_path(self.doc("dictionary.toml"), f"{singular}/{scope}", d.name, "dictionary.toml")
            elif k == "action":
                raw = (self.doc("actions.toml").get("actions") or {}).get(d.name)
            elif k == "pool":
                raw = (self.doc("connectors.toml").get("pools") or {}).get(d.name)
            elif k == "menu_item":
                items = ((self.doc("menus.toml").get("menus") or {}).get(d.scope) or {}).get("items") or []
                raw = next((it for it in items if isinstance(it, dict) and str(it.get("id")) == d.name), None)
        return raw if isinstance(raw, dict) else d.config


def build_package_zip(
    manifest: Manifest,
    *,
    source_install: str = "",
    include: set[tuple[str, str | None, str]] | None = None,
    settings: Any | None = None,
    source_version: str | None = None,
) -> bytes:
    """Serialise *manifest* into a ZIP archive — returns the raw bytes ready to stream.

    When ``include`` is supplied, only deps whose ``(kind, scope, name)`` is in the set are
    written to the TOML files. Seeds are always implicit (the caller passes them in too if
    desired). When ``include`` is None, every dep ships — older behaviour."""
    # Filter the manifest's deps in-place (a shallow copy so the caller's Manifest is
    # untouched) when an include set was passed.
    filtered = manifest
    if include is not None:
        kept = [d for d in manifest.deps if d.key() in include]
        from dataclasses import replace
        filtered = replace(manifest, deps=kept)
    src = _Source(settings)
    files: dict[str, str] = {}
    for name, build in (
        ("connectors.toml", _build_connectors_toml), ("dictionary.toml", _build_dictionary_toml),
        ("screens.toml", _build_screens_toml), ("menus.toml", _build_menus_toml),
        ("charts.toml", _build_charts_toml), ("dashboards.toml", _build_dashboards_toml),
        ("actions.toml", _build_actions_toml), ("explorer.toml", _build_explorer_toml),
        ("notifications.toml", _build_notifications_toml), ("jobs.toml", _build_jobs_toml),
        ("ai.toml", _build_ai_toml),
    ):
        if c := build(filtered, src):
            files[name] = c
    # Machine-readable closure (kinds / counts / deps / missing / warnings) — informational, the
    # same shape the dependency-inspect UI renders, plus where the package comes from (the
    # importer warns when it's a newer framework). The version-history detail view reads it back
    # to show what a stored bundle contains.
    import json
    files["manifest.json"] = json.dumps(
        {**filtered.to_dict(), "source": {"install": source_install, "version": source_version}}, indent=2)
    files["MANIFEST.md"] = _build_manifest_md(filtered, source_install=source_install, files=list(files))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ── per-file assemblers ──────────────────────────────────────────────────────────────────


def _by_kind(manifest: Manifest, kind: str) -> list[Any]:
    return [d for d in manifest.deps if d.kind == kind]


def _trim_queries(connector_cfg: dict[str, Any], wanted: set[str]) -> dict[str, Any]:
    """Keep only the queries / endpoints the manifest references, drop the rest. Preserves
    every other top-level field on the connector (pool / auth / show_in_switcher / home / …).

    *wanted* holds flat synthesised query names (``<base>_<crud>`` for table slots, plus
    custom / sequence / lookup names). Custom queries, sequences and lookups are trimmed by
    name. A **table** is kept whole when any of its four synthesised slot names is wanted —
    the table is the natural unit, and keeping all its slots leaves the export self-contained
    rather than emitting a half-populated table."""
    out = dict(connector_cfg)
    for section in ("queries", "sequences", "lookups"):
        if section in out:
            out[section] = [q for q in out[section] if q.get("name") in wanted]
    if "tables" in out:
        out["tables"] = [
            t for t in out["tables"]
            if {f"{t.get('name')}_{crud}" for crud in ("get", "put", "post", "delete")} & wanted
        ]
    if "endpoints" in out:
        out["endpoints"] = [e for e in out["endpoints"] if e.get("name") in wanted]
    return out


def _build_connectors_toml(manifest: Manifest, src: _Source) -> str:
    """``[connectors.<name>]`` for each connector the package reaches, trimmed to the queries /
    endpoints the seeds use, plus ``[pools.<name>]`` for the selected pools. A connector that isn't
    itself selected ships its items only (no header): the importer merges them into the target's."""
    selected = {c.name: c for c in _by_kind(manifest, "connector")}
    wanted_per_conn: dict[str, set[str]] = {}
    for q in _by_kind(manifest, "query"):
        wanted_per_conn.setdefault(q.scope or "", set()).add(q.name)
    for e in _by_kind(manifest, "api_endpoint"):
        wanted_per_conn.setdefault(e.scope or "", set()).add(e.name)
    out: dict[str, Any] = {}
    # Pools first so they appear at the top of the file.
    pools = _by_kind(manifest, "pool")
    if pools:
        out["pools"] = {p.name: src.value(p) for p in pools}
    conns: dict[str, Any] = {}
    for name in [*selected, *[n for n in wanted_per_conn if n and n not in selected]]:
        full = src.connector(name) or (selected[name].config if name in selected else None)
        if full is None:
            continue
        trimmed = _trim_queries(full, wanted_per_conn.get(name, set()))
        if name not in selected:
            trimmed = {arr: trimmed[arr] for arr in CONNECTOR_ITEMS if trimmed.get(arr)}
        if trimmed:
            conns[name] = trimmed
    if conns:
        out["connectors"] = conns
    return tomli_w.dumps(out, multiline_strings=True) if out else ""


def _build_dictionary_toml(manifest: Manifest, src: _Source) -> str:
    """Assemble dictionary entries / lookups / sequences / enums from every referenced
    dictionary dep, grouping shared at the top and per-connector under ``[connectors.<name>]``."""
    entries = _by_kind(manifest, "dictionary_entry")
    lookups = _by_kind(manifest, "lookup")
    sequences = _by_kind(manifest, "sequence")
    enums = _by_kind(manifest, "enum")
    if not (entries or lookups or sequences or enums):
        return ""
    out: dict[str, Any] = {}
    scoped: dict[str, dict[str, dict[str, Any]]] = {}

    def stash(scope: str, coll: str, name: str, cfg: dict[str, Any]) -> None:
        if scope == "shared":
            out.setdefault(coll, {})[name] = cfg
        else:
            scoped.setdefault(scope, {}).setdefault(coll, {})[name] = cfg

    for d in entries:    stash(d.scope or "shared", "entries", d.name, src.value(d))
    for d in lookups:    stash(d.scope or "shared", "lookups", d.name, src.value(d))
    for d in sequences:  stash(d.scope or "shared", "sequences", d.name, src.value(d))
    for d in enums:      stash(d.scope or "shared", "enums", d.name, src.value(d))
    if scoped:
        out["connectors"] = scoped
    return tomli_w.dumps(out, multiline_strings=True)


def _scoped(manifest: Manifest, src: _Source, kind: str, root: str) -> str:
    """``[<root>.<scope>.<name>]`` for every *kind* dep (screens / charts / dashboards / explorer)."""
    deps = _by_kind(manifest, kind)
    if not deps:
        return ""
    grouped: dict[str, dict[str, Any]] = {}
    for d in deps:
        grouped.setdefault(d.scope or "_unscoped", {})[d.name] = src.value(d)
    return tomli_w.dumps({root: grouped}, multiline_strings=True)


def _flat(manifest: Manifest, src: _Source, kind: str, root: str) -> str:
    """``[<root>.<name>]`` for every *kind* dep (actions / notification rules / AI blocks)."""
    deps = _by_kind(manifest, kind)
    if not deps:
        return ""
    return tomli_w.dumps({root: {d.name: src.value(d) for d in deps}}, multiline_strings=True)


def _build_screens_toml(manifest: Manifest, src: _Source) -> str:
    return _scoped(manifest, src, "screen", "screens")


def _build_actions_toml(manifest: Manifest, src: _Source) -> str:
    return _flat(manifest, src, "action", "actions")


def _build_explorer_toml(manifest: Manifest, src: _Source) -> str:
    return _scoped(manifest, src, "explorer_model", "explorer")


def _build_notifications_toml(manifest: Manifest, src: _Source) -> str:
    return _flat(manifest, src, "notification_rule", "notifications")


def _build_ai_toml(manifest: Manifest, src: _Source) -> str:
    return _flat(manifest, src, "ai", "ai")


def _build_jobs_toml(manifest: Manifest, src: _Source) -> str:
    jobs = _by_kind(manifest, "job")
    if not jobs:
        return ""
    return tomli_w.dumps({"jobs": [src.value(j) for j in jobs]}, multiline_strings=True)


def _build_menus_toml(manifest: Manifest, src: _Source) -> str:
    items = _by_kind(manifest, "menu_item")
    if not items:
        return ""
    # Group items by app and assemble as the AppMenu shape ``{<app>: {items: [...]}}``.
    by_app: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_app.setdefault(it.scope or "_unscoped", []).append(src.value(it))
    out = {"menus": {app: {"items": items_list} for app, items_list in by_app.items()}}
    return tomli_w.dumps(out, multiline_strings=True)


def _build_charts_toml(manifest: Manifest, src: _Source) -> str:
    return _scoped(manifest, src, "chart", "charts")


def _build_dashboards_toml(manifest: Manifest, src: _Source) -> str:
    return _scoped(manifest, src, "dashboard", "dashboards")


# ── MANIFEST.md ──────────────────────────────────────────────────────────────────────────


def _scan_env_refs(values: Iterable[Any], collected: set[str]) -> None:
    """Recursively scan dict / list / str values for ``${VAR}`` references and add the var
    names to *collected*. Used to surface the env vars the operator must set on the target."""
    import re
    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\b[^}]*\}")
    stack: list[Any] = list(values)
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str):
            for m in pattern.finditer(v):
                collected.add(m.group(1))


def _build_manifest_md(manifest: Manifest, *, source_install: str, files: list[str]) -> str:
    """Human-readable summary — what's in the ZIP, what env vars to set, what's missing."""
    by_kind = manifest.by_kind()
    env_refs: set[str] = set()
    _scan_env_refs([d.config for d in manifest.deps], env_refs)

    lines: list[str] = [
        "# Liberty deployment package",
        "",
    ]
    if source_install:
        lines.append(f"**Source install:** `{source_install}`")
        lines.append("")
    lines.append(f"**Seeds:** {len(manifest.seeds)}")
    for s in manifest.seeds:
        lines.append(f"  - {s.kind} `{s.name}`" + (f" (scope `{s.scope}`)" if s.scope else ""))
    lines.append("")
    lines.append("## What's inside")
    lines.append("")
    lines.append(f"Files: {', '.join(f'`{f}`' for f in files)}")
    lines.append("")
    for kind in ("connector", "pool", "query", "api_endpoint", "screen", "menu_item",
                 "dashboard", "chart", "dictionary_entry", "lookup", "sequence", "enum",
                 "action", "explorer_model", "notification_rule", "job", "ai"):
        deps = by_kind.get(kind, [])
        if not deps:
            continue
        lines.append(f"### {kind} ({len(deps)})")
        for d in deps[:20]:
            scope = f"[{d.scope}] " if d.scope and d.scope != "shared" else ""
            lines.append(f"- {scope}`{d.name}`")
        if len(deps) > 20:
            lines.append(f"- … and {len(deps) - 20} more")
        lines.append("")

    if env_refs:
        lines.append("## Environment variables to set on the target")
        lines.append("")
        lines.append("These ``${VAR}`` references are embedded in the package. Make sure each one is")
        lines.append("set in the target install's shell / systemd unit / docker env before reloading:")
        lines.append("")
        for v in sorted(env_refs):
            lines.append(f"- `{v}`")
        lines.append("")

    lines.append("## Secrets")
    lines.append("")
    lines.append("Encrypted values (``ENC:...``) are passed through unchanged. The target install MUST")
    lines.append("carry the SAME ``[crypto] master_key`` / ``LIBERTY_MASTER_KEY`` as the source install,")
    lines.append("otherwise decryption will fail at first use.")
    lines.append("")

    if manifest.missing:
        lines.append("## Missing references (NOT IN PACKAGE)")
        lines.append("")
        lines.append("These references couldn't be resolved against the source install's loaded config.")
        lines.append("They will likely fail on the target too — fix on the source and rebuild:")
        lines.append("")
        for m in manifest.missing[:40]:
            scope = f"[{m['scope']}] " if m.get('scope') else ""
            reason = f" — {m['reason']}" if m.get('reason') else ""
            lines.append(f"- {m['kind']} {scope}`{m['name']}`{reason}")
        lines.append("")

    if manifest.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in manifest.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Apply on the target")
    lines.append("")
    lines.append("1. Unzip into the target install's ``config/`` (or its ``LIBERTY_APPS_DIR``).")
    lines.append("   ```")
    lines.append("   cd $LIBERTY_APPS_DIR  # or the target's config/ dir")
    lines.append("   unzip /path/to/package.zip")
    lines.append("   ```")
    lines.append("2. Set any env vars listed above.")
    lines.append("3. Reload the framework:")
    lines.append("   ```")
    lines.append("   curl -X POST -H 'Authorization: Bearer <superuser-token>' https://<target>/admin/reload")
    lines.append("   ```")
    return "\n".join(lines)
