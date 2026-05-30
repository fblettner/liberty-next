"""Build a deployable ZIP package from a dependency manifest.

Given the closure produced by :mod:`liberty.web.dependencies`, assemble a self-contained
``config/`` slice the operator can rsync to the target install and reload. The package
includes only what the seeds depend on — connectors are trimmed to their referenced
queries, menus include only the items the screens reach, etc.

Secrets are NOT stripped — ``ENC:...`` and ``${VAR}`` references pass through verbatim
so a target install with the same master_key / env vars round-trips cleanly. The
``MANIFEST.md`` lists the env vars the operator must set on the target.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from typing import Any

import tomli_w

from liberty.web.dependencies import Manifest


def build_package_zip(
    manifest: Manifest,
    *,
    source_install: str = "",
    include: set[tuple[str, str | None, str]] | None = None,
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
    files: dict[str, str] = {}
    if c := _build_connectors_toml(filtered):
        files["connectors.toml"] = c
    if c := _build_dictionary_toml(filtered):
        files["dictionary.toml"] = c
    if c := _build_screens_toml(filtered):
        files["screens.toml"] = c
    if c := _build_menus_toml(filtered):
        files["menus.toml"] = c
    if c := _build_charts_toml(filtered):
        files["charts.toml"] = c
    if c := _build_dashboards_toml(filtered):
        files["dashboards.toml"] = c
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
    every other top-level field on the connector (pool / auth / show_in_switcher / home / …)."""
    out = dict(connector_cfg)
    if "queries" in out:
        out["queries"] = [q for q in out["queries"] if q.get("name") in wanted]
    if "endpoints" in out:
        out["endpoints"] = [e for e in out["endpoints"] if e.get("name") in wanted]
    return out


def _build_connectors_toml(manifest: Manifest) -> str:
    """``[connectors.<name>]`` blocks for each referenced connector, plus ``[pools.<name>]``
    blocks for pools they depend on. Connector queries are trimmed to those the seeds use."""
    connectors = _by_kind(manifest, "connector")
    if not connectors:
        return ""
    # Group queries + api_endpoints by connector so we know which to keep.
    wanted_per_conn: dict[str, set[str]] = {}
    for q in _by_kind(manifest, "query"):
        wanted_per_conn.setdefault(q.scope or "", set()).add(q.name)
    for e in _by_kind(manifest, "api_endpoint"):
        wanted_per_conn.setdefault(e.scope or "", set()).add(e.name)
    out: dict[str, Any] = {}
    # Pools first so they appear at the top of the file.
    pools = _by_kind(manifest, "pool")
    if pools:
        out["pools"] = {p.name: p.config for p in pools}
    out["connectors"] = {
        c.name: _trim_queries(c.config, wanted_per_conn.get(c.name, set()))
        for c in connectors
    }
    return tomli_w.dumps(out)


def _build_dictionary_toml(manifest: Manifest) -> str:
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

    for d in entries:    stash(d.scope or "shared", "entries", d.name, d.config)
    for d in lookups:    stash(d.scope or "shared", "lookups", d.name, d.config)
    for d in sequences:  stash(d.scope or "shared", "sequences", d.name, d.config)
    for d in enums:      stash(d.scope or "shared", "enums", d.name, d.config)
    if scoped:
        out["connectors"] = scoped
    return tomli_w.dumps(out)


def _build_screens_toml(manifest: Manifest) -> str:
    screens = _by_kind(manifest, "screen")
    if not screens:
        return ""
    grouped: dict[str, dict[str, Any]] = {}
    for s in screens:
        grouped.setdefault(s.scope or "_unscoped", {})[s.name] = s.config
    return tomli_w.dumps({"screens": grouped})


def _build_menus_toml(manifest: Manifest) -> str:
    items = _by_kind(manifest, "menu_item")
    if not items:
        return ""
    # Group items by app and assemble as the AppMenu shape ``{<app>: {items: [...]}}``.
    by_app: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_app.setdefault(it.scope or "_unscoped", []).append(it.config)
    out = {"menus": {app: {"items": items_list} for app, items_list in by_app.items()}}
    return tomli_w.dumps(out)


def _build_charts_toml(manifest: Manifest) -> str:
    charts = _by_kind(manifest, "chart")
    if not charts:
        return ""
    grouped: dict[str, dict[str, Any]] = {}
    for c in charts:
        grouped.setdefault(c.scope or "_unscoped", {})[c.name] = c.config
    return tomli_w.dumps({"charts": grouped})


def _build_dashboards_toml(manifest: Manifest) -> str:
    dashboards = _by_kind(manifest, "dashboard")
    if not dashboards:
        return ""
    grouped: dict[str, dict[str, Any]] = {}
    for d in dashboards:
        grouped.setdefault(d.scope or "_unscoped", {})[d.name] = d.config
    return tomli_w.dumps({"dashboards": grouped})


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
                 "dashboard", "chart", "dictionary_entry", "lookup", "sequence", "enum"):
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
