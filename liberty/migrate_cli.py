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
or a parseable ``apps_jdbc``); the DB password is a ``${MIGRATED_PW_<NAME>}`` placeholder
(v1 keeps it ``ENC:``-encrypted in ``apps_password`` — set the env var, or recover it with
``liberty-crypto decrypt``). v1's reserved ``default`` pool is skipped: v2's ``[pools.default]``
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
    make_engine,
    merge_connectors,
    migrate_api,
    migrate_column_hints,
    migrate_dictionary,
    migrate_key_columns,
    migrate_menus,
    migrate_pools,
    migrate_sql_queries,
    migrate_table_meta,
    read_api,
    read_applications,
    read_column_hints,
    read_dictionary,
    read_dictionary_rules,
    read_menus,
    read_sql_queries,
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
        parts: list[dict] = []
        if args.command in ("sql", "all"):
            queries, sql_rows = await read_sql_queries(engine)
            tbl_cols, dlg_cols = await read_column_hints(engine)
            tbl_meta, frm_meta = await read_table_meta(engine)
            parts.append(migrate_sql_queries(
                queries, sql_rows, dbtype=args.dbtype, connector_prefix=args.prefix,
                column_hints=migrate_column_hints(tbl_cols, dlg_cols),
                table_meta=migrate_table_meta(tbl_meta, frm_meta),
                key_columns=migrate_key_columns(tbl_cols, dlg_cols),
            ))
        if args.command in ("api", "all"):
            conns, apis, headers, params = await read_api(engine)
            parts.append(migrate_api(conns, apis, headers, params, connector_prefix=args.prefix))
        if args.command in ("sql", "all"):
            # Real [pools.*] from ly_applications — appended last so it overrides the
            # ${LIBERTY_DB_URL_*} stubs that migrate_sql_queries left for referenced pools.
            parts.append(migrate_pools(await read_applications(engine), connector_prefix=args.prefix))
        return merge_connectors(*parts)
    finally:
        await engine.dispose()


def _summary(data: dict, *, command: str) -> str:
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
    if any("MIGRATED_PW_" in str(p.get("url", "")) for p in pools.values()):
        lines.append("# pool URLs carry ${MIGRATED_PW_<NAME>} for the DB password — set the env var(s),")
        lines.append("#   or recover each from v1's ly_applications.apps_password: liberty-crypto decrypt 'ENC:…'")
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
        else:
            p.add_argument("--prefix", default="", help="prepend to migrated connector/pool names (e.g. v1_)")
            if name == "api":
                p.set_defaults(dbtype=None)
            else:
                p.add_argument("--dbtype", default=None, help="only migrate ly_qry_sql rows of this query_dbtype")
            p.set_defaults(default_language="en", connector=None)
        p.add_argument("-o", "--out", help="write the TOML here (default: stdout)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = asyncio.run(_build(args))
    summary = _summary(data, command=args.command)
    text = f"{summary}\n\n{render_toml(data)}"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(summary.replace("# ", ""), file=sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
