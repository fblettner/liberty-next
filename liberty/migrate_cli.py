"""``liberty-migrate`` — turn a v1 Liberty database's ``ly_*`` metadata into v2 config.

    liberty-migrate sql        --source-url <v1-db-url> [--dbtype postgres] [--prefix v1_] [-o out.toml]
    liberty-migrate api        --source-url <v1-db-url> [--prefix v1_] [-o out.toml]
    liberty-migrate all        --source-url <v1-db-url> [--dbtype …] [--prefix …] [-o out.toml]
    liberty-migrate dictionary --source-url <v1-db-url> [--default-language en] [--connector <app>] [-o dictionary.toml]
    liberty-migrate menu       --source-url <v1-db-url> --connector <app> [-o menus.toml]

``--source-url`` is a SQLAlchemy *async* URL — e.g.
``postgresql+asyncpg://user:pw@host/liberty`` for a real v1 DB. v1 is read-only:
this only ``SELECT``s. Output goes to ``--out`` (or stdout); review it, then merge
it into ``config/connectors.toml`` (the ``dictionary`` output → ``config/dictionary.toml``,
the ``menu`` output → ``config/menus.toml``).

``menu`` migrates v1's ``ly_menus`` (+ ``ly_menus_l`` translations) into ``[menus.<app>]`` —
the app's navigation tree (flat, items linked by ``parent``); a query-backed node resolves
through ``ly_tables``/``ly_dlg_frm`` → ``ly_query`` to the matching read query's v2 name, so
run ``liberty-migrate sql`` / ``all`` first (or together). ``--connector`` names the app the
menu belongs to. v1's ``ly_menus_filters`` (per-node role/param filters) isn't migrated yet.

``sql``/``all`` also scaffold ``[pools.*]`` from v1's ``ly_applications`` (one per
``apps_pool``, with a SQLAlchemy URL built from ``apps_host``/``apps_port``/``apps_database``
or a parseable ``apps_jdbc``); the DB password goes in the pool's separate ``password`` field
(v1's ``apps_password`` ``ENC:`` value is carried over verbatim — v2 decrypts it at runtime with
the crypto master key — else a ``${MIGRATED_PW_<NAME>}`` env-var stub). v1's reserved ``default``
pool is skipped: v2's ``[pools.default]``
is v2's own framework DB (the ``ly2_*`` tables). They also carry over **column display hints**
from v1's ``ly_tbl_col`` / ``ly_dlg_col`` (each read query's ``columns`` — order, visibility, a
per-column ``label``/``format`` override) — the labels/types themselves live in the shared
dictionary, so run ``liberty-migrate dictionary`` too (``--connector <app>`` nests its entries
under ``[connectors.<app>.entries.*]`` so several migrated apps don't clash on a ``dd_id``) and
put it at ``config/dictionary.toml``. Migrated API connectors keep v1's ``conn_password`` verbatim
as an ``ENC:`` value — v2 decrypts it at runtime via ``[crypto] master_key``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from liberty.migrations import (
    attach_actions_to_screens,
    make_engine,
    merge_connectors,
    migrate_actions,
    migrate_api,
    migrate_column_hints,
    migrate_column_visibility,
    migrate_context_menus,
    migrate_dictionary,
    migrate_drill_filter_columns,
    migrate_key_columns,
    migrate_menus,
    migrate_screens,
    migrate_pools,
    migrate_sql_queries,
    migrate_lookup_param_names,
    migrate_table_filters,
    migrate_table_meta,
    read_actions,
    read_api,
    read_applications,
    read_context_menus,
    read_column_conditions,
    read_column_hints,
    read_db_schemas,
    read_dictionary,
    read_dictionary_rules,
    read_menus,
    read_screens,
    read_sql_queries,
    read_table_filters,
    read_table_meta,
    render_toml,
)


def _placeholders(data: dict) -> list[str]:
    """Names of ${...} placeholders the operator must still fill in."""
    out: list[str] = []
    import re

    blob = render_toml(data)
    for m in re.finditer(r"\$\{(\w+)(?::-[^}]*)?\}", blob):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


async def _build(args: argparse.Namespace) -> dict:
    engine = make_engine(args.source_url)
    try:
        if args.command == "dictionary":
            entries_rows = await read_dictionary(engine)
            rule_rows = await read_dictionary_rules(engine)
            return migrate_dictionary(
                *entries_rows, *rule_rows,
                default_language=args.default_language, connector_name=args.connector,
            )
        if args.command == "menu":
            return migrate_menus(*await read_menus(engine), app_name=args.connector)
        if args.command == "actions":
            # Pull every v1 actions table + ly_qry_sql (so QUERY tasks resolve to v2 query names).
            action_rows, task_rows, branch_rows, param_rows, task_param_rows, param_filter_rows = (
                await read_actions(engine)
            )
            _, sql_rows = await read_sql_queries(engine)
            return migrate_actions(
                action_rows, task_rows, branch_rows, param_rows, task_param_rows, param_filter_rows,
                sql_rows=sql_rows, app_name=args.connector,
            )
        if args.command == "screen":
            # Pull ly_cdn_params for per-field `visible_when` conditions, plus the v1 context-menu
            # tables (ly_ctxmenus / ly_ctx_val / ly_ctx_filters) so each screen with a
            # ``tbl_ctx_id`` gets its row_menu populated with NavigateActions.
            screen_rows = await read_screens(engine)
            cdn_params = await read_column_conditions(engine)
            ctx_rows, ctx_val_rows, ctx_filter_rows = await read_context_menus(engine)
            # The context-menu migrator needs ly_tables / ly_dlg_frm / ly_qry_sql — these are
            # already in the screen_rows tuple at positions 0, 2, and 7 respectively (see the
            # read_screens docstring for the layout).
            tables_rows, _, dlg_frm_rows = screen_rows[0], screen_rows[1], screen_rows[2]
            sql_rows = screen_rows[7]
            row_menus, promotable_dialogs = migrate_context_menus(
                ctx_rows, ctx_val_rows, ctx_filter_rows,
                tables_rows=tables_rows, dlg_frm_rows=dlg_frm_rows, sql_rows=sql_rows,
                app_name=args.connector,
            )
            screens_data = migrate_screens(
                *screen_rows, cdn_param_rows=cdn_params, row_menus=row_menus,
                promotable_dialogs=promotable_dialogs,
                app_name=args.connector,
            )
            # Auto-attach v1 named actions (NOMAJDE's Create Role / Reset Password / etc.) to
            # the matching v2 screens — match by query-base (an action's task running
            # ``f0092_put`` belongs to the screen whose ``read_query`` is ``f0092_get``).
            # libnsx1 has no rows in ly_actions, so this is a no-op there. The reference dump
            # (full v1 shape including branches / loops / IF / multi-task chains) still requires
            # ``liberty-migrate actions`` to be run separately.
            try:
                act_rows = await read_actions(engine)
            except Exception:
                act_rows = ([], [], [], [], [], [])
            if act_rows[0]:
                actions_data = migrate_actions(
                    *act_rows, sql_rows=sql_rows, app_name=args.connector,
                )
                attach_actions_to_screens(screens_data, actions_data, app_name=args.connector)
            return screens_data
        parts: list[dict] = []
        if args.command in ("sql", "all"):
            queries, sql_rows = await read_sql_queries(engine)
            tbl_cols, dlg_cols = await read_column_hints(engine)
            tbl_meta, frm_meta = await read_table_meta(engine)
            tbl_flt, dlg_flt = await read_table_filters(engine)
            cdn_params = await read_column_conditions(engine)
            # Pull ly_lookup + ly_lkp_params so each lookup-target query gets its WHERE wrap +
            # declared params (UDC etc. — v1's SQL didn't carry its own WHERE).
            _, _, _, lookup_rows, _, _, lookup_params_rows = await read_dictionary_rules(engine)
            # Pull ly_ctx_val + ly_ctx_filters so any column that's a row-context-menu drill
            # target on a destination query gets `filter = True` on that destination — the
            # frontend's URL drill (NavigateAction) then actually filters server-side via
            # `_wrap_with_filters`. Needs ly_tables / ly_dlg_frm to resolve the destination.
            _, ctx_val_rows, ctx_filter_rows = await read_context_menus(engine)
            screen_rows = await read_screens(engine)
            ctx_tables_rows, ctx_dlg_frm_rows = screen_rows[0], screen_rows[2]
            # Threads tbl_col/dlg_col rows in too — used to resolve `flt_target` when it names a
            # dictionary key (col_dd_id) rather than a column (col_target). Without this, queries
            # whose drill-target column has dd_id ≠ col_target (e.g. CFD_APPS_ID with dd APPS_ID)
            # get wrapped with a bogus filter that points at a non-existent column.
            drill_cols = migrate_drill_filter_columns(
                ctx_val_rows, ctx_filter_rows,
                tables_rows=ctx_tables_rows, dlg_frm_rows=ctx_dlg_frm_rows,
                tbl_col_rows=tbl_cols, dlg_col_rows=dlg_cols,
            )
            parts.append(migrate_sql_queries(
                queries, sql_rows, dbtype=args.dbtype, connector_prefix=args.prefix,
                column_hints=migrate_column_hints(tbl_cols, dlg_cols, extra_filter_cols=drill_cols),
                column_filters=migrate_table_filters(tbl_flt, dlg_flt),
                column_visibility=migrate_column_visibility(tbl_cols, dlg_cols, cdn_params),
                table_meta=migrate_table_meta(tbl_meta, frm_meta),
                key_columns=migrate_key_columns(tbl_cols, dlg_cols),
                lookup_params=migrate_lookup_param_names(lookup_rows, lookup_params_rows),
            ))
        if args.command in ("api", "all"):
            conns, apis, headers, params = await read_api(engine)
            parts.append(migrate_api(conns, apis, headers, params, connector_prefix=args.prefix))
        if args.command in ("sql", "all"):
            # Real [pools.*] from ly_applications (+ #SCHEMA.<name># maps from ly_db_schema) — appended
            # last so it overrides the ${LIBERTY_DB_URL_*} stubs migrate_sql_queries left for referenced pools.
            parts.append(migrate_pools(
                await read_applications(engine), db_schemas=await read_db_schemas(engine), connector_prefix=args.prefix,
            ))
        return merge_connectors(*parts)
    finally:
        await engine.dispose()


def _summary(data: dict, *, command: str) -> str:
    if command == "actions":
        apps = data.get("migrated_actions") or {}
        app = next(iter(apps), "?")
        actions = apps.get(app) or {}
        n = len(actions)
        n_tasks = sum(len(a.get("tasks") or []) for a in actions.values())
        n_branches = sum(len(a.get("branches") or []) for a in actions.values())
        n_params = sum(len(a.get("params") or []) for a in actions.values())
        n_warnings = sum(1 for a in actions.values() for t in (a.get("tasks") or []) if t.get("warning"))
        return (f"# migrated: {n} action(s) for [migrated_actions.{app}] — {n_tasks} task(s), "
                f"{n_branches} branch(es), {n_params} action-level param(s), "
                f"{n_warnings} unresolved task target(s) — "
                f"REVIEW: v2 has no IF/LOOP/branch runtime; the operator hand-wires the parts that "
                f"v2's Action union supports (run_query / call_api / notify / refresh) via the "
                f"screen builder. Put this at config/migrated_actions.toml as a reference dump.")
    if command == "dictionary":
        conns = data.get("connectors") or {}
        scope, section = (
            (f"[connectors.{next(iter(conns))}]", next(iter(conns.values())))
            if conns else ("top-level", data)
        )
        entries = section.get("entries") or {}
        enums = section.get("enums") or {}
        lookups = section.get("lookups") or {}
        n_l = sum(len(e.get("l") or {}) for e in entries.values())
        rules = sum(1 for e in entries.values() if e.get("rules"))
        return (f"# migrated: {len(entries)} {scope} dictionary field(s) ({rules} with display rules), "
                f"{len(enums)} enum(s), {len(lookups)} lookup(s), default language "
                f"'{data.get('default_language', 'en')}'" + (f", {n_l} translation(s)" if n_l else "")
                + " — put this at config/dictionary.toml")
    if command == "menu":
        apps = data.get("menus") or {}
        app = next(iter(apps), "?")
        items = (apps.get(app) or {}).get("items") or []
        screens = sum(1 for it in items if it.get("type"))
        n_l = sum(len(it.get("l") or {}) for it in items)
        parents = {it.get("parent") for it in items if it.get("parent")}
        stray = [it["id"] for it in items if not it.get("type") and it["id"] not in parents]
        lines = [
            f"# migrated: {len(items)} item(s) for [menus.{app}] — {screens} screen(s), "
            f"{len(items) - screens} folder(s)" + (f", {n_l} translation(s)" if n_l else "")
            + " — put this at config/menus.toml"
        ]
        if stray:
            lines.append("# folder(s) with no children (e.g. a v1 Dashboard/Chart component we can't map): "
                         + ", ".join(stray) + " — give each children or a `type`/`target`, or drop it")
        return "\n".join(lines)
    if command == "screen":
        apps = data.get("screens") or {}
        app = next(iter(apps), "?")
        screens = apps.get(app) or {}
        n = len(screens)
        with_dlg = sum(1 for s in screens.values() if s.get("dialog"))
        with_audit = sum(1 for s in screens.values() if s.get("audit"))
        cross = sum(1 for s in screens.values() if s.get("connector") and s["connector"] != app)
        n_fields = sum(len(t.get("fields") or []) for s in screens.values() for t in ((s.get("dialog") or {}).get("tabs") or []))
        n_binds = sum(
            len(f.get("lookup_param_binds") or [])
            for s in screens.values()
            for t in ((s.get("dialog") or {}).get("tabs") or [])
            for f in (t.get("fields") or [])
        )
        n_conds = sum(
            1 for s in screens.values()
            for t in ((s.get("dialog") or {}).get("tabs") or [])
            for f in (t.get("fields") or [])
            if f.get("visible_when")
        )
        # Slice 6b: row_menu items migrated from v1's ly_ctxmenus + ly_ctx_val + ly_ctx_filters.
        n_rowmenu_screens = sum(1 for s in screens.values() if s.get("row_menu"))
        n_rowmenu_items = sum(len(s.get("row_menu") or []) for s in screens.values())
        # Nested tabs (v1's FormsDialog / FormsTable inside a FormsDialog) — bumped over their
        # own count + which kind so the operator notices when migration found them.
        n_nested_form = sum(
            1 for s in screens.values()
            for t in ((s.get("dialog") or {}).get("tabs") or [])
            if t.get("type") == "nested_form"
        )
        n_nested_table = sum(
            1 for s in screens.values()
            for t in ((s.get("dialog") or {}).get("tabs") or [])
            if t.get("type") == "nested_table"
        )
        # Promoted row-click targets — screens whose v1 ctx menu had a "Display Properties"-style
        # FormsDialog item; the migrator promoted it to ``row_click_screen`` + dropped the menu entry.
        n_row_click = sum(1 for s in screens.values() if s.get("row_click_screen"))
        # Auto-attached actions — v1 ly_actions wired to a v2 screen via query-base matching
        # (NOMAJDE's "Create Role" → role_management). Each attached entry has an ``id`` that
        # starts with ``migrated_`` so the count is unambiguous.
        n_attached_actions = sum(
            1 for s in screens.values()
            for a in (s.get("actions") or [])
            if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"].startswith("migrated_")
        )
        return (f"# migrated: {n} screen(s) for [screens.{app}] — {with_dlg} with dialog, "
                f"{with_audit} with audit, {cross} cross-connector, {n_fields} dialog field(s), "
                f"{n_binds} param-bind(s), {n_conds} conditional field(s), "
                f"{n_nested_form} nested form tab(s), {n_nested_table} nested table tab(s), "
                f"{n_rowmenu_screens} with row-menu ({n_rowmenu_items} items), "
                f"{n_row_click} promoted row-click(s), {n_attached_actions} auto-attached action(s) — "
                f"put this at config/screens.toml")
    pools = data.get("pools") or {}
    connectors = data.get("connectors") or {}
    queries = [q for c in connectors.values() if c.get("type") == "sql" for q in (c.get("queries") or [])]
    n_q, n_e = len(queries), sum(len(c.get("endpoints") or []) for c in connectors.values() if c.get("type") == "api")
    n_hinted = sum(1 for q in queries if q.get("columns"))
    n_filtered = sum(1 for q in queries if any(c.get("filter") for c in (q.get("columns") or [])))
    blob = render_toml(data)
    lines = [
        f"# migrated: {len(pools)} pool(s), {len(connectors)} connector(s), {n_q} quer(y/ies)"
        f"{f' ({n_hinted} with column hints' if n_hinted else ''}{f', {n_filtered} with server-filter columns' if n_filtered else ''}{')' if n_hinted else ''}, {n_e} endpoint(s)"
    ]
    ph = _placeholders(data)
    if ph:
        lines.append("# fill in these placeholders before use: " + ", ".join(ph))
    if n_hinted:
        lines.append("# column hints reference the shared field dictionary — run `liberty-migrate dictionary")
        lines.append("#   --source-url <same> -o config/dictionary.toml` for the labels/types")
    if any("MIGRATED_PW_" in f"{p.get('url', '')}{p.get('password', '')}" for p in pools.values()):
        lines.append("# some pool `password`s are ${MIGRATED_PW_<NAME>} stubs (v1's apps_password wasn't ENC:) —")
        lines.append("#   set the env var(s), or recover each from v1's ly_applications.apps_password")
    if "ENC:" in blob:
        lines.append("# contains ENC: secrets carried over from v1 — v2 decrypts them at runtime via")
        lines.append("#   [crypto] master_key (set LIBERTY_MASTER_KEY to your v1 MASTER_KEY)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liberty-migrate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in [
        ("sql", "migrate ly_query/ly_qry_sql + ly_applications pools + ly_tbl_col/ly_dlg_col hints"),
        ("api", "migrate ly_api/ly_api_conn"),
        ("all", "sql + api"),
        ("dictionary", "migrate ly_dictionary (+ ly_dictionary_l) → dictionary.toml"),
        ("menu", "migrate ly_menus (+ ly_menus_l) → menus.toml"),
        ("screen", "migrate ly_tables + ly_dlg_frm/_tab/_col/_filters → screens.toml"),
        ("actions", "dump ly_actions named workflows → migrated_actions.toml (for hand-wiring)"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--source-url", required=True, help="SQLAlchemy async URL of the v1 database")
        if name == "dictionary":
            p.add_argument("--default-language", default="en", help="language of v1's ly_dictionary.dd_label (default: en)")
            p.add_argument("--connector", default=None,
                           help="nest the entries under [connectors.<name>.entries.*] (v1 dictionaries were per-app); default: top-level")
            p.set_defaults(prefix="", dbtype=None)
        elif name == "menu":
            p.add_argument("--connector", required=True, help="the app/connector this menu belongs to ([menus.<name>])")
            p.set_defaults(prefix="", dbtype=None, default_language="en")
        elif name == "screen":
            p.add_argument("--connector", required=True, help="the app/connector these screens belong to ([screens.<name>])")
            p.set_defaults(prefix="", dbtype=None, default_language="en")
        elif name == "actions":
            p.add_argument("--connector", required=True, help="the app/connector these actions belong to ([migrated_actions.<name>])")
            p.set_defaults(prefix="", dbtype=None, default_language="en")
        else:
            p.add_argument("--prefix", default="", help="prepend to migrated connector/pool names (e.g. v1_)")
            if name == "api":
                p.set_defaults(dbtype=None)
            else:
                p.add_argument("--dbtype", default=None, help="only migrate ly_qry_sql rows of this query_dbtype")
            p.set_defaults(default_language="en", connector=None)
        p.add_argument("-o", "--out", help="write the TOML here (default: stdout)")
    return parser


def _build_output(data: dict, path: str | None, command: str, summary: str) -> tuple[str, bool]:
    """Produce the TOML text to write. Returns ``(text, merged)`` — ``merged=True`` means the
    output is a comment-preserving merge into an existing file (so other apps' sections survive),
    ``False`` means a fresh render.

    Per-app commands (``dictionary`` / ``menu`` / ``screen`` / ``actions``) migrate one app at a
    time and naturally need merging when the operator targets the same file for several apps:
    nomasx1 first, then nomajde, then a re-run of nomasx1 to pick up new fields, etc. Without
    this, each run **silently replaces** the file — that's how the user lost their nomajde
    screens when we re-ran the nomasx1 migration to test row_click promotion.

    Whole-file commands (``sql`` / ``api`` / ``all``) migrate everything in one go from a single
    v1 DB, so merging doesn't apply — the previous behaviour of replacing the file is kept.
    """
    PER_APP = {"dictionary", "menu", "screen", "actions"}
    fresh = f"{summary}\n\n{render_toml(data)}"
    if not path or command not in PER_APP:
        return fresh, False
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return fresh, False
    import tomlkit
    existing = tomlkit.parse(p.read_text())
    # 2-level merge: a top-level table in the new data has its children merged in (e.g.
    # ``screens.<app>`` replaces just that app, leaving every other app intact); a scalar at
    # the top (e.g. ``default_language`` on the dictionary) gets replaced wholesale.
    for top_key, top_val in data.items():
        if not isinstance(top_val, dict):
            existing[top_key] = top_val
            continue
        if top_key not in existing or not hasattr(existing[top_key], "__setitem__"):
            existing[top_key] = top_val
            continue
        for sub_key, sub_val in top_val.items():
            existing[top_key][sub_key] = sub_val
    # No `# migrated: …` header is prepended on merge — the existing file may carry an older
    # header line (or operator-added prose) we don't want to double-up on. The latest summary
    # still prints to stderr from main().
    return tomlkit.dumps(existing), True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = asyncio.run(_build(args))
    summary = _summary(data, command=args.command)
    text, merged = _build_output(data, args.out, args.command, summary)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(summary.replace("# ", ""), file=sys.stderr)
        action = "merged into" if merged else "wrote"
        print(f"{action} {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
