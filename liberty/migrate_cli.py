"""``liberty-migrate`` — turn a v1 Liberty database's ``ly_*`` metadata into v2
``connectors.toml``.

    liberty-migrate sql  --source-url <v1-db-url> [--dbtype postgres] [--prefix v1_] [-o out.toml]
    liberty-migrate api  --source-url <v1-db-url> [--prefix v1_] [-o out.toml]
    liberty-migrate all  --source-url <v1-db-url> [--dbtype …] [--prefix …] [-o out.toml]

``--source-url`` is a SQLAlchemy *async* URL — e.g.
``postgresql+asyncpg://user:pw@host/liberty`` for a real v1 DB. v1 is read-only:
this only ``SELECT``s. Output goes to ``--out`` (or stdout); review it, then merge
it into ``config/connectors.toml``.

``sql``/``all`` also scaffold ``[pools.*]`` from v1's ``ly_applications`` (one per
``apps_pool``, with a SQLAlchemy URL built from ``apps_host``/``apps_port``/``apps_database``
or a parseable ``apps_jdbc``); the DB password is a ``${MIGRATED_PW_<NAME>}`` placeholder
(v1 keeps it ``ENC:``-encrypted in ``apps_password`` — set the env var, or recover it with
``liberty-crypto decrypt``). v1's reserved ``default`` pool is skipped: v2's ``[pools.default]``
is v2's own framework DB (the ``ly2_*`` tables). They also carry over **column display hints**
from v1's ``ly_tbl_col`` / ``ly_dlg_col`` (display title, visibility, order, a ``format``) onto
each SELECT query's ``columns`` — the result *schema* is still discovered from the query, these
just augment it. Migrated API connectors keep v1's ``conn_password`` verbatim as an ``ENC:``
value — v2 decrypts it at runtime via ``[crypto] master_key`` (set ``LIBERTY_MASTER_KEY`` to your
v1 ``MASTER_KEY``).
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
    migrate_pools,
    migrate_sql_queries,
    read_api,
    read_applications,
    read_column_hints,
    read_sql_queries,
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
        parts: list[dict] = []
        if args.command in ("sql", "all"):
            queries, sql_rows = await read_sql_queries(engine)
            tbl_cols, dlg_cols = await read_column_hints(engine)
            parts.append(migrate_sql_queries(
                queries, sql_rows, dbtype=args.dbtype, connector_prefix=args.prefix,
                column_hints=migrate_column_hints(tbl_cols, dlg_cols),
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


def _summary(data: dict) -> str:
    pools = data.get("pools") or {}
    connectors = data.get("connectors") or {}
    n_q = sum(len(c.get("queries") or []) for c in connectors.values() if c.get("type") == "sql")
    n_e = sum(len(c.get("endpoints") or []) for c in connectors.values() if c.get("type") == "api")
    blob = render_toml(data)
    lines = [f"# migrated: {len(pools)} pool(s), {len(connectors)} connector(s), {n_q} quer(y/ies), {n_e} endpoint(s)"]
    ph = _placeholders(data)
    if ph:
        lines.append("# fill in these placeholders before use: " + ", ".join(ph))
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
    for name, help_ in [("sql", "migrate ly_query/ly_qry_sql + ly_applications pools"), ("api", "migrate ly_api/ly_api_conn"), ("all", "both")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--source-url", required=True, help="SQLAlchemy async URL of the v1 database")
        p.add_argument("--prefix", default="", help="prepend to migrated connector/pool names (e.g. v1_)")
        if name != "api":
            p.add_argument("--dbtype", default=None, help="only migrate ly_qry_sql rows of this query_dbtype")
        else:
            p.set_defaults(dbtype=None)
        p.add_argument("-o", "--out", help="write the TOML here (default: stdout)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = asyncio.run(_build(args))
    toml = render_toml(data)
    text = f"{_summary(data)}\n\n{toml}"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(_summary(data).replace("# ", ""), file=sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
