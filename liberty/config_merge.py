"""Additive, provenance-aware merge of a shipped configuration into a live one.

The licensed apps ship their TOML configuration (screens, dictionary, menus, dashboards, charts,
actions, connectors) in a wheel; the operator edits the live copy through Settings. An upgrade
must bring the product's additions in **without** touching what the operator owns. Whole-file
copy can't do that — it either keeps the live file (product additions never land) or replaces it
(operator edits lost). This module merges **per entity** (a screen, a dictionary entry, a menu
item, a connector, …) with the rule set nomaubl's ``.bundled`` + ``ConfigMerger`` uses:

================================  ==========================================================
situation                         outcome
================================  ==========================================================
new in the wheel                  **added**
live entity has ``override=true`` **kept** (the customer's fork — never touched)
live == what was shipped before   **refreshed** from the wheel (untouched vendor entity)
live changed, no override flag    **kept** and reported (``--force-config`` refreshes it)
withdrawn by the vendor, untouched **removed**
withdrawn but changed locally     **kept** and reported
created by the customer           **kept**
================================  ==========================================================

"What was shipped before" is the snapshot this module writes under ``<config>/.bundled/`` after
every successful install — the wheel's files as delivered. Without one (the first upgrade after
this feature) a locally changed entity is kept and reported rather than replaced — except in
``connectors.toml``, where a vendor connector takes the wheel's structure (secrets restored) as
the previous installer always did; the operator's own connectors are separate entities and stay.

``connectors.toml`` carries per-install secrets the wheel can't ship (``build-wheel.sh`` drops
``[pools.default]`` and blanks every ``ENC:`` value): ``[pools.*]`` is always the live file's
(a wheel-only pool comes in as a stub to fill), and any field the wheel blanked to ``""`` is
restored from the live entity when a connector is refreshed. Comparisons are made with the
secrets restored, so a blanked password never makes an untouched connector look "modified".

Files that aren't entity-shaped (``theme.toml`` — the operator's branding — and the files
under ``presets/`` or the reference tarball) are added when absent, refreshed when identical to
the previous shipment, kept otherwise.

Everything goes through ``tomllib`` + ``tomli_w`` (never ``tomlkit`` — see CLAUDE.md on the
nested-table cliff); comments in the live files are dropped, as every builder save already
does. Liberty Next → Deploy → Package (:mod:`liberty.web.package_import`) shares the entity
enumeration below.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import tomli_w

MERGEABLE = frozenset({
    "connectors.toml", "dictionary.toml", "screens.toml", "menus.toml",
    "charts.toml", "dashboards.toml", "actions.toml", "explorer.toml", "ai.toml",
})
PRESERVED = frozenset({"theme.toml"})  # the operator's branding: added when absent, never refreshed
BUNDLED_DIR = ".bundled"
SECRET_BEARING = "connectors.toml"

EntityKey = tuple[str, str]  # (kind, name) — kind carries the scope: "screen/nomasx1", "entry/shared", …


# ── entity enumeration (shared with Package & Deploy) ────────────────────────────────────────
def enumerate_entities(data: dict[str, Any], filename: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(kind, {name: value})`` for every entity in *data*. Per-file shapes::

        connectors.toml  pools.<name> · connectors.<name>
        dictionary.toml  entries|lookups|sequences|enums.<id> · connectors.<scope>.<same>.<id>
        screens.toml     screens.<app>.<id>
        charts.toml      charts.<scope>.<id>        dashboards.toml  dashboards.<scope>.<id>
        actions.toml     actions.<id>
        explorer.toml    explorer.<app>.<model>
        ai.toml          ai.<app> (the whole block: starters, screens, playbooks)
        notifications.toml  notifications.<rule id>
        menus.toml       menus.<app> (the header: everything but items) · menus.<app>.items[].id
    """
    if filename == "connectors.toml":
        for n, v in (data.get("pools") or {}).items():
            yield "pool", {n: v}
        for n, v in (data.get("connectors") or {}).items():
            yield "connector", {n: v}
    elif filename == "dictionary.toml":
        for coll, singular in (("entries", "entry"), ("lookups", "lookup"), ("sequences", "sequence"), ("enums", "enum")):
            for n, v in (data.get(coll) or {}).items():
                yield f"{singular}/shared", {n: v}
        for scope, section in (data.get("connectors") or {}).items():
            if not isinstance(section, dict):
                continue
            for coll, singular in (("entries", "entry"), ("lookups", "lookup"), ("sequences", "sequence"), ("enums", "enum")):
                for n, v in (section.get(coll) or {}).items():
                    yield f"{singular}/{scope}", {n: v}
    elif filename in ("screens.toml", "charts.toml", "dashboards.toml"):
        root = {"screens.toml": "screens", "charts.toml": "charts", "dashboards.toml": "dashboards"}[filename]
        singular = root[:-1]
        for scope, by_id in (data.get(root) or {}).items():
            if not isinstance(by_id, dict):
                continue
            for n, v in by_id.items():
                yield f"{singular}/{scope}", {n: v}
    elif filename == "actions.toml":
        for n, v in (data.get("actions") or {}).items():
            yield "action", {n: v}
    elif filename == "explorer.toml":
        for app, by_id in (data.get("explorer") or {}).items():
            if not isinstance(by_id, dict):
                continue
            for n, v in by_id.items():
                yield f"explorer/{app}", {n: v}
    elif filename == "ai.toml":
        for app, block in (data.get("ai") or {}).items():
            if isinstance(block, dict):
                yield "ai", {app: block}
    elif filename == "notifications.toml":
        for n, v in (data.get("notifications") or {}).items():
            yield "notification_rule", {n: v}
    elif filename == "menus.toml":
        for app, app_menu in (data.get("menus") or {}).items():
            if not isinstance(app_menu, dict):
                continue
            header = {k: v for k, v in app_menu.items() if k != "items"}
            yield "menu_app", {app: header}
            for it in app_menu.get("items") or []:
                if isinstance(it, dict) and it.get("id") is not None:
                    yield f"menu_item/{app}", {str(it["id"]): it}


def flatten(data: dict[str, Any], filename: str) -> dict[EntityKey, Any]:
    out: dict[EntityKey, Any] = {}
    for kind, items in enumerate_entities(data, filename):
        for name, value in items.items():
            out[(kind, name)] = value
    return out


def resolve_path(kind: str, name: str, filename: str) -> tuple[list[str], str]:
    """``(path_to_parent, leaf_key)`` of a dict-keyed entity inside the TOML tree. Menu items
    live in arrays and are handled by :func:`_menu_items`."""
    if filename == "connectors.toml":
        return (["pools"] if kind == "pool" else ["connectors"]), name
    if filename == "dictionary.toml":
        singular, _, scope = kind.partition("/")
        coll = {"entry": "entries", "lookup": "lookups", "sequence": "sequences", "enum": "enums"}[singular]
        return ([coll] if scope == "shared" else ["connectors", scope, coll]), name
    if filename in ("screens.toml", "charts.toml", "dashboards.toml"):
        _, _, scope = kind.partition("/")
        return [{"screens.toml": "screens", "charts.toml": "charts", "dashboards.toml": "dashboards"}[filename], scope], name
    if filename == "actions.toml":
        return ["actions"], name
    if filename == "explorer.toml":
        _, _, scope = kind.partition("/")
        return ["explorer", scope], name
    if filename == "ai.toml":
        return ["ai"], name
    if filename == "notifications.toml":
        return ["notifications"], name
    if filename == "menus.toml" and kind == "menu_app":
        return ["menus"], name
    raise KeyError(f"{kind!r} is not a dict-keyed entity of {filename}")


def get_path(data: dict[str, Any], kind: str, name: str, filename: str) -> Any:
    parents, leaf = resolve_path(kind, name, filename)
    node: Any = data
    for p in parents:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return None
    return node.get(leaf) if isinstance(node, dict) else None


def set_path(data: dict[str, Any], kind: str, name: str, value: Any, filename: str) -> None:
    parents, leaf = resolve_path(kind, name, filename)
    node = data
    for p in parents:
        node = node.setdefault(p, {})
    if kind == "menu_app":  # the header merges over the live app dict, items untouched
        items = node.get(leaf, {}).get("items") if isinstance(node.get(leaf), dict) else None
        node[leaf] = {**value, **({"items": items} if items is not None else {})}
    else:
        node[leaf] = value


def delete_path(data: dict[str, Any], kind: str, name: str, filename: str) -> None:
    parents, leaf = resolve_path(kind, name, filename)
    node: Any = data
    for p in parents:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(leaf, None)


def is_overridden(entity: Any) -> bool:
    """``override = true`` — the customer's fork, never touched by a vendor refresh."""
    return isinstance(entity, dict) and entity.get("override") is True


# ── secrets ──────────────────────────────────────────────────────────────────────────────────
# A whole value that is one `${VAR}` or `${VAR:-default}` reference: "the install owns this" (a
# host, a user, the AIS TLS check `${JDE_AIS_VERIFY_SSL:-true}` — the default only covers an unset
# variable); a concrete live value at that place is the install's choice and wins.
_ENV_PLACEHOLDER = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$")


def is_placeholder(value: Any) -> bool:
    """``""`` or a whole ``${VAR}`` / ``${VAR:-default}`` reference — what the wheel ships where
    the install owns the value (a blanked ``ENC:`` secret, a host, a user, a TLS switch)."""
    return isinstance(value, str) and (value == "" or bool(_ENV_PLACEHOLDER.match(value)))


def restore_blanked(node: Any, live: Any) -> int:
    """Where *node* holds a placeholder (``""`` or a whole ``${VAR}`` reference) and *live* has
    its own value at the same path (a string — a different reference included —, a boolean, a
    number), copy the live value in: the wheel ships ``ENC:``
    secrets blanked and install-specific hosts / users as ``${VAR}`` references, the install
    owns the real value. Recurses into nested tables, leaves arrays alone (secrets never live
    in TOML arrays). Returns how many were restored."""
    if not isinstance(node, dict) or not isinstance(live, dict):
        return 0
    n = 0
    for k, v in node.items():
        lv = live.get(k)
        # the install's value: a concrete one (text, boolean, number), or a reference of its own
        # (another AIS on ``${AIS_PROD_URL}``) — only a blank or the very same reference is not
        concrete = (isinstance(lv, str) and lv != "" and lv != v) or (isinstance(lv, (bool, int, float)) and not isinstance(v, (bool, int, float)))
        if is_placeholder(v) and concrete:
            node[k] = lv
            n += 1
        elif isinstance(v, dict) and isinstance(lv, dict):
            n += restore_blanked(v, lv)
    return n


def _with_live_secrets(value: Any, live: Any) -> tuple[Any, int]:
    """A copy of *value* with the blanks filled from *live* + how many were filled."""
    copy = json.loads(json.dumps(value, default=str))
    return copy, restore_blanked(copy, live)


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


# ── per-file merge ───────────────────────────────────────────────────────────────────────────
@dataclass
class FileMergeReport:
    file: str
    added: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    refreshed_forced: list[str] = field(default_factory=list)   # changed locally, --force-config won
    kept_override: list[str] = field(default_factory=list)
    kept_modified: list[str] = field(default_factory=list)      # changed locally, no flag — kept, review
    kept_custom: list[str] = field(default_factory=list)        # the customer's own entities
    kept_withdrawn: list[str] = field(default_factory=list)     # vendor dropped it, customer changed it
    removed: list[str] = field(default_factory=list)            # vendor dropped it, untouched
    unchanged: int = 0
    secrets_restored: int = 0
    pools_kept: int = 0
    had_snapshot: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.refreshed or self.refreshed_forced or self.removed)

    def to_dict(self) -> dict[str, Any]:
        d = {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}
        d["changed"] = self.changed
        return d

    def summary(self) -> str:
        parts = [f"{len(self.added)} added", f"{len(self.refreshed) + len(self.refreshed_forced)} refreshed",
                 f"{len(self.kept_override)} override", f"{len(self.kept_modified) + len(self.kept_withdrawn)} kept (modified)",
                 f"{len(self.kept_custom)} custom", f"{len(self.removed)} removed", f"{self.unchanged} unchanged"]
        if self.pools_kept:
            parts.append(f"{self.pools_kept} pool(s) kept")
        if self.secrets_restored:
            parts.append(f"{self.secrets_restored} secret(s) restored")
        return ", ".join(parts)


def _label(kind: str, name: str) -> str:
    return f"{kind}: {name}"


def merge_file(
    filename: str, wheel: dict[str, Any], live: dict[str, Any] | None, bundled_prev: dict[str, Any] | None,
    *, force_vendor: bool = False,
) -> tuple[dict[str, Any], FileMergeReport]:
    """Merge *wheel* (the new shipment) into *live* (the operator's file) knowing *bundled_prev*
    (the previous shipment, or None). Returns ``(merged_doc, report)``; nothing is written."""
    rep = FileMergeReport(file=filename, had_snapshot=bundled_prev is not None)
    if live is None:  # nothing to merge into: the wheel's file as is
        merged = json.loads(json.dumps(wheel, default=str))
        for (kind, name) in flatten(wheel, filename):
            rep.added.append(_label(kind, name))
        return merged, rep

    merged = json.loads(json.dumps(live, default=str))
    secret = filename == SECRET_BEARING
    w_ents, l_ents = flatten(wheel, filename), flatten(live, filename)
    p_ents = flatten(bundled_prev, filename) if bundled_prev is not None else None

    def apply(kind: str, name: str, value: Any) -> None:
        if kind.startswith("menu_item/"):
            _menu_items(merged, kind, name, value)
        else:
            set_path(merged, kind, name, value, filename)

    def drop(kind: str, name: str) -> None:
        if kind.startswith("menu_item/"):
            _menu_items(merged, kind, name, None)
        else:
            delete_path(merged, kind, name, filename)

    for (kind, name), w_val in w_ents.items():
        l_val = l_ents.get((kind, name))
        label = _label(kind, name)
        if (kind, name) not in l_ents:
            apply(kind, name, w_val)
            rep.added.append(label)
            continue
        if secret and kind == "pool":  # per-install: credentials, hosts, schemas, dblinks — the live one wins
            rep.pools_kept += 1
            rep.unchanged += 1
            continue
        if is_overridden(l_val):
            rep.kept_override.append(label)
            continue
        w_cmp, n_secrets = _with_live_secrets(w_val, l_val) if secret else (w_val, 0)
        if _same(w_cmp, l_val):
            rep.unchanged += 1
            continue
        p_val = p_ents.get((kind, name)) if p_ents is not None else None
        untouched = p_val is not None and _same(_with_live_secrets(p_val, l_val)[0] if secret else p_val, l_val)
        if secret and p_ents is None:
            # No shipment on record and a vendor connector (queries, SQL) differs from the wheel: these
            # are vendor-owned by construction (the operator's own connectors are separate entities,
            # kept as custom above) and the previous installer always took the wheel's structure here
            # with the secrets restored — keep doing that, so the first upgrade after this feature
            # still brings the new queries in.
            untouched = True
        if untouched or force_vendor:
            apply(kind, name, w_cmp)
            rep.secrets_restored += n_secrets
            (rep.refreshed if untouched else rep.refreshed_forced).append(label)
        else:
            rep.kept_modified.append(label)

    for (kind, name), l_val in l_ents.items():
        if (kind, name) in w_ents:
            continue
        label = _label(kind, name)
        if secret and kind == "pool":
            rep.pools_kept += 1
            continue
        if p_ents is not None and (kind, name) in p_ents:  # the vendor shipped it before and dropped it
            p_val = p_ents[(kind, name)]
            if not is_overridden(l_val) and _same(_with_live_secrets(p_val, l_val)[0] if secret else p_val, l_val):
                drop(kind, name)
                rep.removed.append(label)
            else:
                rep.kept_withdrawn.append(label)
        else:
            rep.kept_custom.append(label)

    # top-level scalars / sections that aren't entities (dictionary's default_language,
    # framework_enums, …): the live value wins, the wheel fills what's missing
    for k, v in wheel.items():
        if k not in merged and not isinstance(v, dict):
            merged[k] = v
            rep.notes.append(f"setting {k} added")
    return merged, rep


# ── nomaflow jobs (plugins/<app>/jobs.toml) ──────────────────────────────────────────────────
# A job mixes what the vendor owns (the steps, what they call, the description, tags, the user
# parameters) with what the operator sets from the Jobs page (whether and when it runs). Merging
# the job as one entity would let a schedule block every product fix to its steps, so the merge is
# per field: the vendor part is refreshed like any entity, the operator fields stay the operator's.
JOB_OPERATOR_FIELDS = ("enabled", "schedule", "timezone", "presets", "alerts", "retry", "log_level", "timeout_seconds")


def _job_vendor_part(job: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in job.items() if k not in JOB_OPERATOR_FIELDS and k not in ("params", "override")}
    out["params"] = sorted((job.get("params") or {}).keys())  # the vendor owns which params exist
    return out


def _three_way(key: str, wheel: dict[str, Any], live: dict[str, Any], prev: dict[str, Any] | None) -> tuple[bool, Any]:
    """The operator's value of *key*: ``(present, value)``. With the previous shipment on record,
    the live value wins only where the operator changed it (else the vendor's new value lands);
    without one, whatever the live file has wins."""
    if prev is not None and live.get(key) == prev.get(key):
        return key in wheel, wheel.get(key)
    return key in live, live.get(key)


def _merge_job(wheel: dict[str, Any], live: dict[str, Any], prev: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """The wheel's job with the operator's fields and parameter values kept. Returns the job and
    the notes (operator fields kept, preset entries dropped because their step is gone)."""
    out = {k: v for k, v in wheel.items() if k not in JOB_OPERATOR_FIELDS}
    kept: list[str] = []
    for f in JOB_OPERATOR_FIELDS:
        present, value = _three_way(f, wheel, live, prev)
        if present:
            out[f] = value
        if present and value != wheel.get(f) or not present and f in wheel:
            kept.append(f)
    w_params, l_params = wheel.get("params") or {}, live.get("params") or {}
    p_params = (prev.get("params") or {}) if prev is not None else None
    params: dict[str, Any] = {}
    for k in w_params:
        present, value = _three_way(k, w_params, l_params, p_params)
        params[k] = value if present else w_params[k]
        if params[k] != w_params[k]:
            kept.append(f"params.{k}")
    if params:
        out["params"] = params
    notes = [f"kept your {', '.join(kept)}"] if kept else []
    steps = {st.get("name") for st in wheel.get("steps") or [] if isinstance(st, dict)}
    for pr in out.get("presets") or []:
        for key in ("step_enabled", "op_kwargs"):
            gone = [n for n in (pr.get(key) or {}) if n not in steps]
            for n in gone:
                del pr[key][n]
            if gone:
                notes.append(f"preset {pr.get('name')!r}: dropped {key} of removed step(s) {', '.join(gone)}")
    return out, notes


def merge_jobs(
    wheel: dict[str, Any], live: dict[str, Any] | None, bundled_prev: dict[str, Any] | None,
    *, filename: str = "jobs.toml", force_vendor: bool = False,
) -> tuple[dict[str, Any], FileMergeReport]:
    """Merge a shipped ``jobs.toml`` into the live one. Per job (keyed by ``id``):

    - new in the wheel → added; ``override = true`` on the live job → kept as is;
    - in both → the wheel's job with the operator's fields kept (:data:`JOB_OPERATOR_FIELDS`, and
      the values of the params the wheel still declares); if the operator also edited the vendor
      part (steps…) since the previous shipment, the job is kept and reported (``force_vendor``
      takes the wheel's) — without a previous shipment on record the wheel's vendor part wins,
      as the whole-file copy always did;
    - withdrawn by the vendor → removed when its vendor part is untouched, kept and reported when
      edited; a job the vendor never shipped (no previous shipment, or absent from it) is the
      operator's own and kept.

    ``[meta]`` (retention / missed-run policies) is the operator's: the live one wins. The jobs
    come out in the wheel's order, the operator's own after them."""
    rep = FileMergeReport(file=filename, had_snapshot=bundled_prev is not None)
    w_jobs = [j for j in wheel.get("jobs") or [] if isinstance(j, dict) and j.get("id")]
    if live is None:
        rep.added += [_label("job", j["id"]) for j in w_jobs]
        return json.loads(json.dumps(wheel, default=str)), rep
    live = json.loads(json.dumps(live, default=str))
    l_by = {j["id"]: j for j in live.get("jobs") or [] if isinstance(j, dict) and j.get("id")}
    p_by = {j["id"]: j for j in (bundled_prev or {}).get("jobs") or [] if isinstance(j, dict) and j.get("id")}
    w_ids = {j["id"] for j in w_jobs}
    out_jobs: list[dict[str, Any]] = []
    for w in w_jobs:
        jid, label = w["id"], _label("job", w["id"])
        lv = l_by.get(jid)
        if lv is None:
            out_jobs.append(w)
            rep.added.append(label)
            continue
        if lv.get("override") is True:
            out_jobs.append(lv)
            rep.kept_override.append(label)
            continue
        pv = p_by.get(jid) if bundled_prev is not None else None
        vendor_edited = pv is not None and _job_vendor_part(lv) != _job_vendor_part(pv) and _job_vendor_part(lv) != _job_vendor_part(w)
        if vendor_edited and not force_vendor:
            out_jobs.append(lv)
            rep.kept_modified.append(label)
            continue
        merged, notes = _merge_job(w, lv, pv)
        out_jobs.append(merged)
        rep.notes += [f"{jid}: {n}" for n in notes]
        if _same(merged, lv):
            rep.unchanged += 1
        else:
            (rep.refreshed_forced if vendor_edited else rep.refreshed).append(label)
    for jid, lv in l_by.items():
        if jid in w_ids:
            continue
        label = _label("job", jid)
        pv = p_by.get(jid)
        if pv is None:
            out_jobs.append(lv)
            rep.kept_custom.append(label)
        elif lv.get("override") is not True and _job_vendor_part(lv) == _job_vendor_part(pv):
            rep.removed.append(label)
        else:
            out_jobs.append(lv)
            rep.kept_withdrawn.append(label)
    merged_doc = {k: v for k, v in live.items() if k != "jobs"}
    for k, v in wheel.items():
        if k != "jobs" and k not in merged_doc:
            merged_doc[k] = v
    merged_doc["jobs"] = out_jobs
    return merged_doc, rep


def merge_jobs_file(wheel_path: Path, live_doc: dict[str, Any] | None, dst: Path, snapshot: Path,
                    *, force_vendor: bool = False, dry_run: bool = False) -> FileMergeReport:
    """File-level :func:`merge_jobs` for the apps installer: *live_doc* is the operator's jobs file
    read BEFORE the plugin package was replaced, *snapshot* the previous shipment's copy (under
    ``<config>/.bundled/``). Writes the merged file to *dst* and records the shipment in
    *snapshot* (unless *dry_run*)."""
    wheel = _load_toml(wheel_path) or {}
    merged, rep = merge_jobs(wheel, live_doc, _load_toml(snapshot), filename=str(dst.name), force_vendor=force_vendor)
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(tomli_w.dumps(merged, multiline_strings=True), encoding="utf-8")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheel_path, snapshot)
    return rep


def _menu_items(doc: dict[str, Any], kind: str, item_id: str, value: Any | None) -> None:
    """Set (``value``) or remove (``None``) one ``[[menus.<app>.items]]`` entry by id, appending
    new items at the end so the operator's ordering is kept."""
    _, _, app = kind.partition("/")
    app_menu = doc.setdefault("menus", {}).setdefault(app, {})
    items = app_menu.setdefault("items", [])
    for i, it in enumerate(items):
        if isinstance(it, dict) and str(it.get("id")) == item_id:
            if value is None:
                items.pop(i)
            else:
                items[i] = value
            return
    if value is not None:
        items.append(value)


# ── directory merge ──────────────────────────────────────────────────────────────────────────
@dataclass
class MergeReport:
    files: list[FileMergeReport] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)       # non-mergeable files added
    refreshed_files: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)    # non-mergeable files kept (exist, differ)
    dry_run: bool = False
    snapshot_dir: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> list[str]:
        out = []
        for f in self.files:
            out += [f"{f.file} — {x}" for x in f.kept_modified + f.kept_withdrawn]
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run, "snapshot_dir": self.snapshot_dir, "errors": list(self.errors),
            "files": [f.to_dict() for f in self.files], "copied": list(self.copied),
            "refreshed_files": list(self.refreshed_files), "preserved": list(self.preserved),
            "needs_review": self.needs_review,
        }

    def format_plan(self) -> str:
        head = "Merge plan (dry run — nothing written)" if self.dry_run else "Merge result"
        lines = [head, ""]
        for f in self.files:
            lines.append(f"  {f.file:<18} {f.summary()}" + ("" if f.had_snapshot else "   [no previous shipment on record]"))
            for x in f.added[:20]:
                lines.append(f"      + {x}")
            if len(f.added) > 20:
                lines.append(f"      + … {len(f.added) - 20} more")
            for x in f.refreshed + f.refreshed_forced:
                lines.append(f"      ↻ {x}")
            for x in f.removed:
                lines.append(f"      − {x}")
            for x in f.kept_modified + f.kept_withdrawn:
                lines.append(f"      ! kept, changed locally without override: {x}")
        for x in self.copied:
            lines.append(f"  + {x}")
        for x in self.refreshed_files:
            lines.append(f"  ↻ {x}")
        for x in self.preserved:
            lines.append(f"  = {x} (kept — differs from the shipment)")
        for e in self.errors:
            lines.append(f"  ✗ {e}")
        if self.needs_review:
            lines += ["", f"  {len(self.needs_review)} entit(y/ies) changed locally without `override = true` were kept.",
                      "  Review them in Settings; set `override` to keep them for good, or re-run with --force-config to take the vendor's."]
        return "\n".join(lines)


def _load_toml(p: Path) -> dict[str, Any] | None:
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    return tomllib.loads(text) if text.strip() else {}


def merge_config_dir(
    wheel_dir: Path, live_dir: Path, *, force_vendor: bool = False, dry_run: bool = False,
    version: str | None = None,
) -> MergeReport:
    """Merge every file the wheel ships (*wheel_dir*) into the operator's config dir (*live_dir*),
    then record the shipment under ``live_dir/.bundled/`` (unless *dry_run*). Never deletes a file."""
    report = MergeReport(dry_run=dry_run)
    bundled = live_dir / BUNDLED_DIR
    staged: list[tuple[Path, Path]] = []  # (wheel file, live target) to snapshot after writing

    for src in sorted(p for p in wheel_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(wheel_dir)
        if rel.parts[0] in ("__pycache__", ".DS_Store") or src.name == ".DS_Store":
            continue
        dst = live_dir / rel
        prev = bundled / rel
        if len(rel.parts) == 1 and rel.name in MERGEABLE:
            try:
                wheel_doc = _load_toml(src) or {}
                live_doc = _load_toml(dst)
                prev_doc = _load_toml(prev)
            except tomllib.TOMLDecodeError as exc:
                report.errors.append(f"{rel}: not valid TOML, skipped — {exc}")
                continue
            merged, frep = merge_file(rel.name, wheel_doc, live_doc, prev_doc, force_vendor=force_vendor)
            report.files.append(frep)
            if not dry_run and (frep.changed or live_doc is None):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(tomli_w.dumps(merged, multiline_strings=True), encoding="utf-8")
            staged.append((src, dst))
            continue
        # not entity-shaped: theme.toml, presets/*, the reference tarball, …
        if not dst.exists():
            report.copied.append(str(rel))
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif rel.name in PRESERVED or len(rel.parts) == 1 and rel.name.endswith(".toml") and rel.name not in MERGEABLE:
            if not _same_bytes(src, dst):
                report.preserved.append(str(rel))
        elif _same_bytes(src, dst):
            pass  # identical already
        elif prev.is_file() and _same_bytes(prev, dst):  # untouched since the previous shipment → refresh
            report.refreshed_files.append(str(rel))
            if not dry_run:
                shutil.copy2(src, dst)
        elif force_vendor:
            report.refreshed_files.append(str(rel))
            if not dry_run:
                shutil.copy2(src, dst)
        else:
            report.preserved.append(str(rel))
        staged.append((src, dst))

    if not dry_run:
        bundled.mkdir(parents=True, exist_ok=True)
        for src, _dst in staged:
            out = bundled / src.relative_to(wheel_dir)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
        (bundled / "manifest.json").write_text(json.dumps({
            "version": version, "files": sorted(str(s.relative_to(wheel_dir)) for s, _ in staged),
        }, indent=2), encoding="utf-8")
        report.snapshot_dir = str(bundled)
    return report


def bundled_version(config_dir: Path | str | None) -> str | None:
    """The apps bundle version recorded by the last install (``<config>/.bundled/manifest.json``).
    On Docker the wheel is materialised into the mount, not pip-installed in the running
    container, so the package metadata is absent — this file is the source of truth there."""
    if not config_dir:
        return None
    try:
        return json.loads((Path(config_dir) / BUNDLED_DIR / "manifest.json").read_text(encoding="utf-8")).get("version") or None
    except (OSError, ValueError):
        return None


def apps_version(config_dir: Path | str | None) -> str | None:
    """Installed apps bundle version: the pip metadata when the wheel is installed in this
    interpreter (pipx / venv), else the materialised bundle's manifest, else None."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("liberty-apps")
    except PackageNotFoundError:
        return bundled_version(config_dir)


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False
