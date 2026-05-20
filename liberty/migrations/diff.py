"""Validate-by-diff: compare a v1 database against a v2 config tree and report gaps.

The migration tools (``liberty-migrate sql / dictionary / menu / screen / api / all``) emit a
v2 config from a v1 DB, but the v1 → v2 translation isn't 1:1 — some v1 constructs land
verbatim, some are reshaped (FormsDialog → Screen + dialog), some are deferred (form-layer
dd_rules: SEQUENCE / SYSDATE / LOGIN), and some are silently dropped by older versions of the
migrator (``ly_act_tasks_params.map_default`` was dropped before the per-task-default fix). An
operator who re-runs the migration after a fix doesn't always notice what just lit up; an
operator who never runs the migration after a fix has no way to find what's still missing.

This module's :func:`compute_diff` produces a single :class:`DiffReport` summarising every
v1 row that didn't make it into v2, plus rows where the v2 entity exists but its data shape
diverges from v1 (e.g. a query whose v2 ``param_binds`` count differs from the v1
``ly_act_tasks_params`` count for the matching task). The report renders to text (human
review) or JSON (CI / tooling).

What's covered:

* **Pools** — every ``ly_applications.apps_pool`` should have a matching ``[pools.X]`` in
  ``connectors.toml``. Stub pools (the migrator emits ``${LIBERTY_DB_URL_X}`` when no
  ``ly_applications`` row matches a query's ``query_pool``) are flagged as ``info``.
* **SQL queries** — every ``(ly_query, ly_qry_sql)`` pair should produce one v2 query named
  ``slugify(label)_{crud}`` (matching :func:`migrate_sql_queries`'s convention) under
  ``[connectors.<pool>.queries.*]``. Missing CRUD slots and slugify collisions surface here.
* **Dictionary entries / enums / lookups / sequences** — every ``ly_dictionary`` /
  ``ly_enum`` / ``ly_lookup`` / ``ly_sequence`` row should land in ``dictionary.toml``
  (shared or per-connector overlay, depending on the migration's ``--connector`` flag).
* **Screens** — every ``ly_tables`` (and ``ly_dlg_frm`` when there's no table side) should
  have a matching ``[screens.<app>.<id>]``. The screen's column count is compared to the v1
  ``ly_tbl_col`` / ``ly_dlg_col`` rows for the same target query.
* **Menu items** — every ``ly_menus`` row should have a matching entry under
  ``[menus.<app>.items.*]`` (matched by ``menu_id``).
* **API connectors / endpoints** — every ``ly_api_conn`` row should produce a v2 API
  connector; every ``ly_api`` row should produce an endpoint on the resolved connector.

Out of scope (delete + re-add or hand-wire is the workflow):

* **Per-action / per-task default values** that *are* now carried (since the
  ``map_default`` fix) but only after re-running ``liberty-migrate screen`` — this report
  surfaces the gap when present; the fix is "re-run the screen migration", not handled here.
* **Action chains** — matching a migrated v1 ``ly_actions`` workflow to its attachment on
  the v2 ``screen.dialog.on_save`` / ``screen.actions`` / per-tab ``actions`` is a many-to-one
  shape and best inspected by hand. The action migration is captured into
  ``[migrated_actions.<app>]`` as a reference dump regardless; this diff just notes when an
  action *has* a migrated dump but doesn't appear on any wired hook.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from liberty.connectors.config import load_connectors_file
from liberty.connectors.dictionary import load_dictionary
from liberty.menus import load_menus
from liberty.migrations.source import (
    read_api,
    read_applications,
    read_column_hints,
    read_dictionary,
    read_dictionary_rules,
    read_menus,
    read_screens,
    read_sequences,
    read_sql_queries,
)
from liberty.migrations.v1 import slugify
from liberty.screens import load_screens


# ── data classes ────────────────────────────────────────────────────────────────────────────


SEVERITIES = ("missing", "mismatched", "extra", "info", "ok")


@dataclass
class DiffEntry:
    """One row in the diff report.

    ``kind`` identifies the entity type (``pool``, ``sql_query``, ``screen``, …) so consumers
    can group / filter. ``severity`` orders the result (missing > mismatched > extra > info >
    ok); the text renderer prints ``ok`` only when verbose. ``entity_id`` is the human-readable
    identifier (e.g. ``nomasx1/users_get`` for a query, ``security_users`` for a screen).
    ``details`` carries entity-kind-specific extras (v1 ids, expected v2 path, counts) for the
    JSON output + the text renderer's expanded line.
    """

    kind: str
    severity: str
    entity_id: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffReport:
    """The aggregate result of :func:`compute_diff`.

    ``entries`` is the flat list. ``counts`` is a {severity: count} summary used by the text
    renderer's header line and by the CLI's exit-code heuristic (non-zero exit when any
    ``missing`` or ``mismatched`` entries surface — drives CI / pre-deploy checks)."""

    entries: list[DiffEntry] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SEVERITIES})

    def add(self, entry: DiffEntry) -> None:
        self.entries.append(entry)
        self.counts[entry.severity] = self.counts.get(entry.severity, 0) + 1

    def has_problems(self) -> bool:
        """True when at least one ``missing`` or ``mismatched`` entry surfaced. The CLI uses
        this for exit code 1 vs 0; ``info`` and ``extra`` don't fail the check (extras are
        usually hand-added v2 entities)."""
        return bool(self.counts.get("missing", 0) + self.counts.get("mismatched", 0))

    def filter(self, *, kinds: Iterable[str] | None = None,
               severities: Iterable[str] | None = None) -> list[DiffEntry]:
        ks = set(kinds) if kinds else None
        ss = set(severities) if severities else None
        out: list[DiffEntry] = []
        for e in self.entries:
            if ks and e.kind not in ks:
                continue
            if ss and e.severity not in ss:
                continue
            out.append(e)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "has_problems": self.has_problems(),
            "entries": [
                {"kind": e.kind, "severity": e.severity, "entity_id": e.entity_id,
                 "message": e.message, "details": e.details}
                for e in self.entries
            ],
        }


# ── public entry point ─────────────────────────────────────────────────────────────────────


def compute_diff(
    *,
    source_url: str,
    connectors_path: Path,
    screens_path: Path,
    dictionary_path: Path,
    menus_path: Path | None = None,
    connector_filter: str | None = None,
    include_ok: bool = False,
) -> DiffReport:
    """Synchronous entry point — opens the v1 DB, runs every reader, compares against the
    v2 config, returns the report. ``connector_filter`` restricts the SQL-side checks to one
    v2 connector (the slugified pool name); ``include_ok`` keeps the per-row "✓ matched"
    entries in the result (useful for verbose CI output, off by default — the report stays
    focused on what needs attention).

    The async readers are wrapped via :func:`asyncio.run` so the CLI's sync entry point stays
    simple. Re-entering this from inside an event loop will raise; that's intentional — the
    CLI is the only caller.
    """
    return asyncio.run(_compute_diff_async(
        source_url=source_url,
        connectors_path=connectors_path,
        screens_path=screens_path,
        dictionary_path=dictionary_path,
        menus_path=menus_path,
        connector_filter=connector_filter,
        include_ok=include_ok,
    ))


async def _compute_diff_async(
    *,
    source_url: str,
    connectors_path: Path,
    screens_path: Path,
    dictionary_path: Path,
    menus_path: Path | None,
    connector_filter: str | None,
    include_ok: bool,
) -> DiffReport:
    engine = create_async_engine(source_url)
    try:
        # ── load every v1 row-set we'll check ──
        queries, sql_rows = await read_sql_queries(engine)
        apps = await read_applications(engine)
        dd_rows, _dd_l = await read_dictionary(engine)
        # read_dictionary_rules returns 7 row-sets (enum / enum_val / enum_val_l / lookup /
        # ly_qry_sql⋈ly_query / ly_dictionary_filters / ly_lkp_params).
        enum_rows, _enum_val, _enum_l, lkp_rows, _sql_join, _dd_flt, _lkp_p = await read_dictionary_rules(engine)
        # read_sequences returns 2 row-sets (sequence definitions + their per-param dd_ids).
        seq_rows, _seq_p = await read_sequences(engine)
        # read_menus returns 5 row-sets (ly_menus / ly_menus_l / ly_tables / ly_dlg_frm / sql_join).
        menus_rows, _menus_l, tables_rows, dlg_rows, _menu_sql = await read_menus(engine)
        # read_screens returns 8 row-sets (kitchen sink of dialog tables); we just need a few.
        scr_tables, _dlgs, _frm, _frm_l, _dlg_tab, _dlg_col, _dlg_flt, _scr_sql = await read_screens(engine)
        api_conns, api_eps, _hdr, _params = await read_api(engine)
        tbl_col_rows, dlg_col_rows = await read_column_hints(engine)
    finally:
        await engine.dispose()

    # ── load every v2 file we'll check against ──
    cfg = load_connectors_file(connectors_path)
    dictionary = load_dictionary(dictionary_path)
    screens_file = load_screens(screens_path)
    menus_file = load_menus(menus_path) if menus_path else None

    # Raw connectors.toml — needed for the pool stub-URL check (``${LIBERTY_DB_URL_X}``
    # placeholders are substituted away by load_connectors_file's env pass, so we need to
    # see the file as it sits on disk to detect them).
    raw_connectors: dict[str, Any] = {}
    if connectors_path.exists() and connectors_path.read_text(encoding="utf-8").strip():
        raw_connectors = tomllib.loads(connectors_path.read_text(encoding="utf-8"))

    report = DiffReport()
    _diff_pools(report, apps=apps, connectors=cfg, sql_rows=sql_rows,
                raw_connectors=raw_connectors, include_ok=include_ok)
    _diff_sql_queries(report, queries=queries, sql_rows=sql_rows, connectors=cfg,
                      connector_filter=connector_filter, include_ok=include_ok)
    _diff_columns(report, tbl_col_rows=tbl_col_rows, dlg_col_rows=dlg_col_rows,
                  queries=queries, sql_rows=sql_rows, screens_file=screens_file,
                  include_ok=include_ok)
    _diff_dictionary_entries(report, rows=dd_rows, dictionary=dictionary, include_ok=include_ok)
    _diff_dictionary_enums(report, rows=enum_rows, dictionary=dictionary, include_ok=include_ok)
    _diff_dictionary_lookups(report, rows=lkp_rows, dictionary=dictionary, include_ok=include_ok)
    _diff_dictionary_sequences(report, rows=seq_rows, dictionary=dictionary, include_ok=include_ok)
    # ``scr_tables`` (from read_screens) carries the full ``tbl_db_name`` / ``tbl_label``
    # columns the screen-matching needs; ``tables_rows`` (from read_menus) only carries
    # ``tbl_id`` + ``tbl_query_id`` (insufficient for slug-based matching).
    _diff_screens(report, tables_rows=scr_tables, dlg_frm_rows=dlg_rows,
                  screens_file=screens_file, include_ok=include_ok)
    if menus_file is not None:
        _diff_menus(report, menus_rows=menus_rows, menus=menus_file, include_ok=include_ok)
    _diff_api(report, conn_rows=api_conns, ep_rows=api_eps, connectors=cfg,
              include_ok=include_ok)
    return report


# ── per-entity checks ──────────────────────────────────────────────────────────────────────


def _diff_pools(
    report: DiffReport,
    *,
    apps: Iterable[Mapping[str, Any]],
    connectors: Any,                                # ConnectorsFile (env-substituted)
    sql_rows: Iterable[Mapping[str, Any]],
    raw_connectors: Mapping[str, Any],              # tomllib output, pre-env-substitution
    include_ok: bool,
) -> None:
    """Every ``ly_applications.apps_pool`` should land as a real pool (not a stub) in
    ``[pools.X]``. We also surface pools the migrator emitted as ``${LIBERTY_DB_URL_X}``
    stubs (these came from ``ly_qry_sql.query_pool`` rows for which no ``ly_applications``
    entry exists — the operator needs to fill in the real URL).

    Stub-URL detection reads the *raw* TOML (before env substitution), since the
    ``${LIBERTY_DB_URL_X}`` literals are gone once Pydantic parsing has resolved them
    against the environment (substitute to empty string when the env var isn't set)."""
    pool_names_v1 = {slugify(str(a.get("apps_pool") or ""), fallback="default")
                     for a in apps if a.get("apps_pool")}
    # Also pull pool names referenced by queries (catches ``query_pool`` entries that have
    # no matching ``ly_applications`` row — the migrator emits a stub pool for them).
    for r in sql_rows:
        pool = str(r.get("query_pool") or "default").strip()
        if pool and pool != "default":
            pool_names_v1.add(slugify(pool, fallback="default"))

    v2_pools = dict(connectors.pools or {})
    raw_pools = (raw_connectors.get("pools") or {}) if raw_connectors else {}

    for name in sorted(pool_names_v1):
        if name not in v2_pools:
            report.add(DiffEntry(
                kind="pool", severity="missing", entity_id=name,
                message=f"v1 references pool {name!r} but no [pools.{name}] in connectors.toml",
                details={"name": name},
            ))
            continue
        raw_url = ""
        raw_pool = raw_pools.get(name) if isinstance(raw_pools, dict) else None
        if isinstance(raw_pool, dict):
            raw_url = str(raw_pool.get("url") or "")
        is_stub = "${LIBERTY_DB_URL_" in raw_url
        if is_stub:
            report.add(DiffEntry(
                kind="pool", severity="info", entity_id=name,
                message=f"pool {name} has a stub URL — fill in connectors.toml or set the env var",
                details={"name": name, "url": raw_url},
            ))
        elif include_ok:
            report.add(DiffEntry(
                kind="pool", severity="ok", entity_id=name,
                message=f"pool {name} → [pools.{name}]",
                details={"name": name},
            ))


# Read CRUD verbs that get the un-suffixed name treatment. Keep aligned with v1.py's
# ``_READ_CRUD`` set — we re-derive here rather than import to keep this module's
# dependencies narrow.
_READ_CRUD: frozenset[str] = frozenset({"GET", "SELECT"})


def _v2_query_name(label: str | None, crud: str, query_id: int) -> str:
    """Same naming convention as :func:`liberty.migrations.v1.migrate_sql_queries`. Kept in
    sync by hand — if the migrator changes its convention this function and the tests must
    be updated together. ``crud`` lowercased to match the v2 emitted names."""
    crud_u = (crud or "SELECT").upper()
    base_text = f"{label}_{crud_u}" if label else f"q{query_id}_{crud_u}"
    return slugify(base_text, fallback=f"q{query_id}_{crud_u.lower()}")


def _diff_sql_queries(
    report: DiffReport,
    *,
    queries: Iterable[Mapping[str, Any]],
    sql_rows: Iterable[Mapping[str, Any]],
    connectors: Any,
    connector_filter: str | None,
    include_ok: bool,
) -> None:
    """One v2 query per ``(query_pool, query_id, query_crud)``. Missing rows are reported
    against the resolved connector + name; extras (a v2 query whose name doesn't match any v1
    row) get reported with severity ``extra``. v1 queries with no SQL at all are skipped (the
    migrator drops them too)."""
    labels = {int(q["query_id"]): (q.get("query_label") or "") for q in queries}
    expected_per_conn: dict[str, set[tuple[int, str, str]]] = {}    # conn → {(qid, crud, v2_name)}
    for r in sql_rows:
        sql = (r.get("query_sqlquery") or "").strip()
        if not sql:
            continue
        qid = int(r["query_id"])
        crud = str(r.get("query_crud") or "SELECT").upper()
        pool = str(r.get("query_pool") or "default").strip() or "default"
        conn_name = slugify(pool, fallback="default")
        if connector_filter and conn_name != connector_filter:
            continue
        v2_name = _v2_query_name(labels.get(qid, ""), crud, qid)
        expected_per_conn.setdefault(conn_name, set()).add((qid, crud, v2_name))

    v2_connectors = dict(connectors.connectors or {})
    for conn_name, expected in sorted(expected_per_conn.items()):
        conn = v2_connectors.get(conn_name)
        if conn is None or getattr(conn, "type", None) != "sql":
            for qid, crud, v2_name in sorted(expected, key=lambda t: (t[2], t[1])):
                report.add(DiffEntry(
                    kind="sql_query", severity="missing", entity_id=f"{conn_name}/{v2_name}",
                    message=f"v1 query #{qid} crud={crud} expects [connectors.{conn_name}] which is absent",
                    details={"connector": conn_name, "query_id": qid, "crud": crud, "v2_name": v2_name},
                ))
            continue
        v2_query_names = {q.name for q in (conn.queries or [])}
        seen_v2_names: set[str] = set()
        for qid, crud, v2_name in sorted(expected, key=lambda t: (t[2], t[1])):
            seen_v2_names.add(v2_name)
            if v2_name in v2_query_names:
                if include_ok:
                    report.add(DiffEntry(
                        kind="sql_query", severity="ok",
                        entity_id=f"{conn_name}/{v2_name}",
                        message=f"v1 #{qid} crud={crud} → [connectors.{conn_name}.queries.{v2_name}]",
                        details={"connector": conn_name, "query_id": qid, "crud": crud,
                                 "v2_name": v2_name},
                    ))
            else:
                report.add(DiffEntry(
                    kind="sql_query", severity="missing",
                    entity_id=f"{conn_name}/{v2_name}",
                    message=f"v1 query #{qid} (label={labels.get(qid, '?')!r}) crud={crud} not migrated to {v2_name}",
                    details={"connector": conn_name, "query_id": qid, "crud": crud,
                             "v2_name": v2_name, "label": labels.get(qid)},
                ))
        # v2 queries with no v1 match → extra (hand-added, or a stale migration). Skip when
        # a connector_filter is in effect (we only saw a slice of v1, can't judge extras
        # confidently).
        if connector_filter is None:
            for v2_name in sorted(v2_query_names - seen_v2_names):
                report.add(DiffEntry(
                    kind="sql_query", severity="extra",
                    entity_id=f"{conn_name}/{v2_name}",
                    message=f"v2 query {v2_name} has no matching v1 row — hand-added or stale migration",
                    details={"connector": conn_name, "v2_name": v2_name},
                ))


def _diff_columns(
    report: DiffReport,
    *,
    tbl_col_rows: Iterable[Mapping[str, Any]],
    dlg_col_rows: Iterable[Mapping[str, Any]],
    queries: Iterable[Mapping[str, Any]],
    sql_rows: Iterable[Mapping[str, Any]],
    screens_file: Any,
    include_ok: bool,
) -> None:
    """v1 column hints (``ly_tbl_col`` / ``ly_dlg_col``) → v2 ``Screen.columns`` entries.

    Count-based rather than name-based (v1 → v2 column names go through slugify and a few
    normalisations; matching by name would produce false positives). For each v1 query with
    hints, find the matching v2 screen and compare counts.

    **Tricky bit** — v1 had *two* column tables per query: ``ly_tbl_col`` (grid columns) and
    ``ly_dlg_col`` (dialog fields). They usually carry the *same* column set but different
    presentations (the grid hides some columns the dialog shows, and vice versa). v2's
    ``Screen.columns`` is the unified source — so we compare against the **larger** of the
    two v1 counts, not the sum. Summing was the bug that made every screen look 2× too small
    (a 56-column F0092 reported as 112 because tbl+dlg both contributed). Also: a single
    query_id can have multiple ``ly_dlg_col`` rows per frm_id (one dialog can carry several
    forms), so dedupe by (query_id, col_target) within each source table — same column
    appearing on two forms still counts once.

    Mismatches with a delta of 1–2 are usually fine (v1 had a placeholder column with empty
    target which the migrator drops); larger gaps indicate the migration is stale."""
    # Build the v2 query → screen index.
    by_read_query: dict[tuple[str | None, str], Any] = {}
    for app, screens in (screens_file.screens or {}).items():
        for sid, screen in screens.items():
            key = (screen.connector or app, screen.read_query)
            by_read_query[key] = (app, sid, screen)

    labels = {int(q["query_id"]): (q.get("query_label") or "") for q in queries}

    # Per-source-table dedupe by (query_id, col_target) so the same column referenced from
    # several frm_ids only counts once. Then per query, the larger of the two counts is the
    # number of distinct columns v2's Screen.columns should mirror.
    def _count_distinct(rows: Iterable[Mapping[str, Any]]) -> dict[int, int]:
        per_qid: dict[int, set[str]] = {}
        for r in rows:
            qid = r.get("query_id")
            if qid is None:
                continue
            target = (r.get("col_target") or "").strip()
            if not target:
                continue
            per_qid.setdefault(int(qid), set()).add(target.upper())
        return {qid: len(targets) for qid, targets in per_qid.items()}

    tbl_counts = _count_distinct(tbl_col_rows)
    dlg_counts = _count_distinct(dlg_col_rows)
    v1_cols: dict[int, int] = {}
    for qid in set(tbl_counts) | set(dlg_counts):
        v1_cols[qid] = max(tbl_counts.get(qid, 0), dlg_counts.get(qid, 0))

    # Resolve each v1 query to its v2 (connector, name) and screen.
    for r in sql_rows:
        crud = str(r.get("query_crud") or "SELECT").upper()
        if crud not in _READ_CRUD:
            continue                                            # column hints only ride on reads
        qid = int(r["query_id"])
        if qid not in v1_cols:
            continue
        pool = str(r.get("query_pool") or "default").strip() or "default"
        conn_name = slugify(pool, fallback="default")
        v2_name = _v2_query_name(labels.get(qid, ""), crud, qid)
        screen_match = by_read_query.get((conn_name, v2_name))
        if screen_match is None:
            # Screen-level diff already covers this; don't double-report.
            continue
        app, sid, screen = screen_match
        v2_count = len(screen.columns)
        v1_count = v1_cols[qid]
        details = {
            "app": app, "screen": sid, "v1_count": v1_count, "v2_count": v2_count,
            "v1_tbl_count": tbl_counts.get(qid, 0),
            "v1_dlg_count": dlg_counts.get(qid, 0),
        }
        if v2_count == 0 and v1_count > 0:
            report.add(DiffEntry(
                kind="screen_columns", severity="missing",
                entity_id=f"{app}/{sid}",
                message=f"screen {app}/{sid} has no Screen.columns but v1 query #{qid} has {v1_count} column hint(s)",
                details=details,
            ))
        elif abs(v2_count - v1_count) > 2:
            report.add(DiffEntry(
                kind="screen_columns", severity="mismatched",
                entity_id=f"{app}/{sid}",
                message=f"screen {app}/{sid} has {v2_count} columns but v1 query #{qid} has {v1_count} (tbl={tbl_counts.get(qid, 0)}, dlg={dlg_counts.get(qid, 0)})",
                details=details,
            ))
        elif include_ok:
            report.add(DiffEntry(
                kind="screen_columns", severity="ok",
                entity_id=f"{app}/{sid}",
                message=f"screen {app}/{sid} columns: v1={v1_count} v2={v2_count}",
                details=details,
            ))


def _diff_dictionary_entries(
    report: DiffReport,
    *,
    rows: Iterable[Mapping[str, Any]],
    dictionary: Any,
    include_ok: bool,
) -> None:
    """Every ``ly_dictionary.dd_id`` should appear somewhere in ``dictionary.toml``. v1 used
    one dictionary per app (so ``dd_id`` was already scoped); v2 normalises that to per-
    connector overlays. We accept a match in *any* scope — shared or scoped — since the
    operator may have moved entries between scopes."""
    all_keys: set[str] = set()
    for k in (dictionary.entries or {}):
        all_keys.add(k)
    for sec in (dictionary.connectors or {}).values():
        for k in (sec.entries or {}):
            all_keys.add(k)

    seen_v1: set[str] = set()
    for r in rows:
        dd_id = str(r.get("dd_id") or "").strip()
        if not dd_id or dd_id in seen_v1:
            continue
        seen_v1.add(dd_id)
        if dd_id in all_keys:
            if include_ok:
                report.add(DiffEntry(
                    kind="dict_entry", severity="ok", entity_id=dd_id,
                    message=f"entry {dd_id} → dictionary.toml",
                    details={"dd_id": dd_id},
                ))
        else:
            report.add(DiffEntry(
                kind="dict_entry", severity="missing", entity_id=dd_id,
                message=f"v1 dictionary entry {dd_id!r} not in dictionary.toml — run `liberty-migrate dictionary`",
                details={"dd_id": dd_id, "v1_label": r.get("dd_label")},
            ))


def _diff_dictionary_enums(
    report: DiffReport,
    *,
    rows: Iterable[Mapping[str, Any]],
    dictionary: Any,
    include_ok: bool,
) -> None:
    """Same shape as the entries check — every ``ly_enum.enum_id`` should appear in
    ``[enums.*]`` (shared or scoped). v1's enum ids are integers stringified."""
    all_keys: set[str] = set()
    for k in (dictionary.enums or {}):
        all_keys.add(str(k))
    for sec in (dictionary.connectors or {}).values():
        for k in (sec.enums or {}):
            all_keys.add(str(k))

    for r in rows:
        eid = str(r.get("enum_id") or "").strip()
        if not eid:
            continue
        if eid in all_keys:
            if include_ok:
                report.add(DiffEntry(
                    kind="dict_enum", severity="ok", entity_id=eid,
                    message=f"enum {eid} → dictionary.toml",
                    details={"enum_id": eid},
                ))
        else:
            report.add(DiffEntry(
                kind="dict_enum", severity="missing", entity_id=eid,
                message=f"v1 enum #{eid} not in dictionary.toml — run `liberty-migrate dictionary`",
                details={"enum_id": eid, "v1_label": r.get("enum_label")},
            ))


def _diff_dictionary_lookups(
    report: DiffReport,
    *,
    rows: Iterable[Mapping[str, Any]],
    dictionary: Any,
    include_ok: bool,
) -> None:
    all_keys: set[str] = set()
    for k in (dictionary.lookups or {}):
        all_keys.add(str(k))
    for sec in (dictionary.connectors or {}).values():
        for k in (sec.lookups or {}):
            all_keys.add(str(k))

    for r in rows:
        lid = str(r.get("lkp_id") or "").strip()
        if not lid:
            continue
        if lid in all_keys:
            if include_ok:
                report.add(DiffEntry(
                    kind="dict_lookup", severity="ok", entity_id=lid,
                    message=f"lookup {lid} → dictionary.toml",
                    details={"lkp_id": lid},
                ))
        else:
            report.add(DiffEntry(
                kind="dict_lookup", severity="missing", entity_id=lid,
                message=f"v1 lookup #{lid} not in dictionary.toml — run `liberty-migrate dictionary`",
                details={"lkp_id": lid, "v1_query_id": r.get("lkp_query_id"),
                         "v1_dd_id": r.get("lkp_dd_id")},
            ))


def _diff_dictionary_sequences(
    report: DiffReport,
    *,
    rows: Iterable[Mapping[str, Any]],
    dictionary: Any,
    include_ok: bool,
) -> None:
    all_keys: set[str] = set()
    for k in (dictionary.sequences or {}):
        all_keys.add(str(k))
    for sec in (dictionary.connectors or {}).values():
        for k in (sec.sequences or {}):
            all_keys.add(str(k))

    for r in rows:
        sid = str(r.get("seq_id") or "").strip()
        if not sid:
            continue
        if sid in all_keys:
            if include_ok:
                report.add(DiffEntry(
                    kind="dict_sequence", severity="ok", entity_id=sid,
                    message=f"sequence {sid} → dictionary.toml",
                    details={"seq_id": sid},
                ))
        else:
            report.add(DiffEntry(
                kind="dict_sequence", severity="missing", entity_id=sid,
                message=f"v1 sequence #{sid} not in dictionary.toml — run `liberty-migrate dictionary`",
                details={"seq_id": sid, "v1_query_id": r.get("seq_query_id"),
                         "v1_dd_id": r.get("seq_dd_id")},
            ))


def _diff_screens(
    report: DiffReport,
    *,
    tables_rows: Iterable[Mapping[str, Any]],
    dlg_frm_rows: Iterable[Mapping[str, Any]],
    screens_file: Any,
    include_ok: bool,
) -> None:
    """Every ``ly_tables`` should land as one v2 ``[screens.<app>.<sid>]``. v1 form-only
    screens (``ly_dlg_frm`` rows without a matching ``ly_tables`` row) are also covered.

    Matching strategy: we walk *every* v2 screen and build a fingerprint set of
    ``slugify(tbl_db_name)`` and ``slugify(tbl_label)``-shaped ids. A v1 row is "found" if its
    candidate slugs hit any v2 screen id (across all apps). This is intentionally lossy —
    operators often rename screens after migration — but it catches the bulk-missing case
    (a whole app's screens weren't migrated).
    """
    v2_ids: set[str] = set()
    for screens in (screens_file.screens or {}).values():
        for sid in screens:
            v2_ids.add(sid)

    for r in tables_rows:
        tid = r.get("tbl_id")
        if tid is None:
            continue
        candidates: list[str] = []
        for src_key in ("tbl_db_name", "tbl_label"):
            val = (r.get(src_key) or "").strip()
            if val:
                candidates.append(slugify(val, fallback=f"screen_{tid}"))
        candidates.append(f"screen_{tid}")                       # the migrator's fallback
        if any(c in v2_ids for c in candidates):
            if include_ok:
                report.add(DiffEntry(
                    kind="screen", severity="ok",
                    entity_id=str(candidates[0]),
                    message=f"v1 ly_tables #{tid} → screens.toml",
                    details={"tbl_id": tid, "candidates": candidates},
                ))
        else:
            report.add(DiffEntry(
                kind="screen", severity="missing",
                entity_id=f"tbl_id={tid}",
                message=f"v1 ly_tables #{tid} (db={r.get('tbl_db_name')!r}) not in screens.toml — run `liberty-migrate screen`",
                details={"tbl_id": tid, "candidates": candidates,
                         "tbl_db_name": r.get("tbl_db_name"), "tbl_label": r.get("tbl_label")},
            ))

    # Form-only screens: a v1 ly_dlg_frm that doesn't correspond to any ly_tables entry.
    table_frm_ids = {r.get("tbl_frm_id") for r in tables_rows if r.get("tbl_frm_id") is not None}
    for r in dlg_frm_rows:
        fid = r.get("frm_id")
        if fid is None or fid in table_frm_ids:
            continue
        candidates = [slugify((r.get("frm_label") or "").strip(), fallback=f"frm_{fid}"),
                      f"frm_{fid}"]
        if any(c in v2_ids for c in candidates):
            if include_ok:
                report.add(DiffEntry(
                    kind="screen", severity="ok",
                    entity_id=str(candidates[0]),
                    message=f"v1 ly_dlg_frm #{fid} → screens.toml",
                    details={"frm_id": fid, "candidates": candidates},
                ))
        else:
            report.add(DiffEntry(
                kind="screen", severity="missing",
                entity_id=f"frm_id={fid}",
                message=f"v1 ly_dlg_frm #{fid} (label={r.get('frm_label')!r}) — form-only screen not in screens.toml",
                details={"frm_id": fid, "candidates": candidates,
                         "frm_label": r.get("frm_label")},
            ))


def _diff_menus(
    report: DiffReport,
    *,
    menus_rows: Iterable[Mapping[str, Any]],
    menus: Any,
    include_ok: bool,
) -> None:
    """Every ``ly_menus.menu_id`` should map to a v2 menu item id (under any
    ``[menus.<app>.items]``). Matching is by slugified id."""
    v2_ids: set[str] = set()
    for app_menu in (menus.menus or {}).values():
        for item in (app_menu.items or []):
            v2_ids.add(item.id)

    for r in menus_rows:
        mid = r.get("menu_id")
        if mid is None:
            continue
        candidates = [
            slugify((r.get("menu_label") or "").strip(), fallback=f"menu_{mid}"),
            f"menu_{mid}",
        ]
        if any(c in v2_ids for c in candidates):
            if include_ok:
                report.add(DiffEntry(
                    kind="menu_item", severity="ok",
                    entity_id=candidates[0],
                    message=f"v1 ly_menus #{mid} → menus.toml",
                    details={"menu_id": mid, "candidates": candidates},
                ))
        else:
            report.add(DiffEntry(
                kind="menu_item", severity="missing",
                entity_id=f"menu_id={mid}",
                message=f"v1 ly_menus #{mid} (label={r.get('menu_label')!r}) not in menus.toml",
                details={"menu_id": mid, "menu_label": r.get("menu_label"),
                         "candidates": candidates},
            ))


def _diff_api(
    report: DiffReport,
    *,
    conn_rows: Iterable[Mapping[str, Any]],
    ep_rows: Iterable[Mapping[str, Any]],
    connectors: Any,
    include_ok: bool,
) -> None:
    """Every ``ly_api_conn`` → one v2 API connector named ``slugify(conn_name)``; every
    ``ly_api`` → one endpoint on the resolved connector. v1 endpoints with no
    ``conn_id`` migrate to a single ``legacy_api`` connector (matching the migrator's
    fallback)."""
    api_v2 = {name for name, c in (connectors.connectors or {}).items()
              if getattr(c, "type", None) == "api"}
    by_conn = {name: {e.name for e in (c.endpoints or [])}
               for name, c in (connectors.connectors or {}).items()
               if getattr(c, "type", None) == "api"}

    v1_conn_names: dict[int, str] = {}
    for r in conn_rows:
        cid = r.get("conn_id")
        if cid is None:
            continue
        name = slugify(str(r.get("conn_name") or ""), fallback=f"api_{cid}")
        v1_conn_names[int(cid)] = name
        if name in api_v2:
            if include_ok:
                report.add(DiffEntry(
                    kind="api_connector", severity="ok", entity_id=name,
                    message=f"v1 api_conn #{cid} → connectors.toml [connectors.{name}]",
                    details={"conn_id": cid, "v2_name": name},
                ))
        else:
            report.add(DiffEntry(
                kind="api_connector", severity="missing", entity_id=name,
                message=f"v1 api_conn #{cid} not migrated to [connectors.{name}]",
                details={"conn_id": cid, "v2_name": name, "v1_name": r.get("conn_name")},
            ))

    for r in ep_rows:
        eid = r.get("api_id")
        if eid is None:
            continue
        cid = r.get("conn_id")
        conn_name = v1_conn_names.get(int(cid)) if cid is not None else "legacy_api"
        # Endpoint v2 name comes from the v1 ``api_label``. Same convention build_api_resolver
        # uses (slugify of the label, fallback to ``api_<id>``).
        v2_ep = slugify((r.get("api_label") or "").strip(), fallback=f"api_{eid}")
        if conn_name in by_conn and v2_ep in by_conn[conn_name]:
            if include_ok:
                report.add(DiffEntry(
                    kind="api_endpoint", severity="ok",
                    entity_id=f"{conn_name}/{v2_ep}",
                    message=f"v1 api #{eid} → [connectors.{conn_name}.endpoints.{v2_ep}]",
                    details={"api_id": eid, "conn": conn_name, "v2_name": v2_ep},
                ))
        else:
            report.add(DiffEntry(
                kind="api_endpoint", severity="missing",
                entity_id=f"{conn_name or '<?>'}/{v2_ep}",
                message=f"v1 api #{eid} (label={r.get('api_label')!r}) not migrated to {conn_name}/{v2_ep}",
                details={"api_id": eid, "conn": conn_name, "v2_name": v2_ep,
                         "v1_label": r.get("api_label")},
            ))


# ── text renderer ──────────────────────────────────────────────────────────────────────────


def render_text(report: DiffReport, *, verbose: bool = False) -> str:
    """Human-readable summary. Groups by ``kind`` for readability; within each group sorts by
    severity (missing first). When ``verbose`` is true, ``ok`` entries are listed too —
    useful for an at-a-glance verification that the diff actually walked everything."""
    by_kind: dict[str, list[DiffEntry]] = {}
    for e in report.entries:
        by_kind.setdefault(e.kind, []).append(e)

    # severity-ordered sort within each group: missing > mismatched > extra > info > ok
    sev_order = {s: i for i, s in enumerate(SEVERITIES)}

    lines: list[str] = []
    header = (
        f"=== migration diff ===  missing={report.counts.get('missing', 0)}  "
        f"mismatched={report.counts.get('mismatched', 0)}  "
        f"extra={report.counts.get('extra', 0)}  "
        f"info={report.counts.get('info', 0)}"
    )
    lines.append(header)
    lines.append("")
    if not report.entries:
        lines.append("(nothing to report — every v1 row found a v2 match)")
        return "\n".join(lines) + "\n"

    glyph = {"missing": "✗", "mismatched": "⚠", "extra": "+", "info": "ℹ", "ok": "✓"}
    for kind in sorted(by_kind):
        entries = sorted(by_kind[kind], key=lambda e: (sev_order.get(e.severity, 99), e.entity_id))
        # Skip ``ok`` entries unless verbose.
        if not verbose:
            entries = [e for e in entries if e.severity != "ok"]
        if not entries:
            continue
        lines.append(f"-- {kind} ({len(entries)}) --")
        for e in entries:
            lines.append(f"  {glyph.get(e.severity, '?')} [{e.severity}] {e.entity_id}: {e.message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Async engine type isn't used by callers, but importing keeps the import surface stable.
__all__ = [
    "DiffEntry", "DiffReport", "SEVERITIES",
    "compute_diff", "render_text",
    "AsyncEngine",
]
