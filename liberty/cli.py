"""``liberty-connectors`` — list and run connectors from the command line.

    liberty-connectors list
    liberty-connectors describe <connector>
    liberty-connectors run <connector> <query-or-endpoint> [-p key=value ...]

Useful for poking at a ``connectors.toml`` without booting the web layer; also
the smoke test for Phase 1. Reads ``config/app.toml`` for the connectors-file
path (override with ``--config``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from liberty.config import load_settings
from liberty.connectors import APIConnector, ConnectorRegistry, SQLConnector, load_connectors
from liberty.connectors.base import ConnectorError


def _parse_params(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--param expects key=value, got: {item!r}")
        out[key.strip()] = value
    return out


def _registry(args: argparse.Namespace) -> ConnectorRegistry:
    if args.config:
        return load_connectors(args.config)
    settings = load_settings()
    return load_connectors(settings.connectors.config_path)


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


async def _cmd_list(args: argparse.Namespace) -> int:
    registry = _registry(args)
    try:
        _emit(
            {
                "pools": registry.pools.names(),
                "connectors": [
                    {"name": c["name"], "type": c["type"], "items": _item_names(c)}
                    for c in registry.describe()
                ],
            }
        )
        return 0
    finally:
        await registry.aclose()


def _item_names(desc: dict[str, Any]) -> list[str]:
    if desc["type"] == "sql":
        return [q["name"] for q in desc["queries"]]
    return [e["name"] for e in desc["endpoints"]]


async def _cmd_describe(args: argparse.Namespace) -> int:
    registry = _registry(args)
    try:
        _emit(registry.get(args.connector).describe())
        return 0
    finally:
        await registry.aclose()


async def _cmd_run(args: argparse.Namespace) -> int:
    registry = _registry(args)
    params = _parse_params(args.param)
    try:
        conn = registry.get(args.connector)
        if isinstance(conn, SQLConnector):
            result = await conn.execute(args.name, params)
            _emit(result.to_dict())
        elif isinstance(conn, APIConnector):
            result = await conn.call(args.name, params)
            _emit(result.to_dict())
            if not result.success:
                return 1
        else:  # pragma: no cover
            raise SystemExit(f"Unknown connector kind: {type(conn).__name__}")
        return 0
    finally:
        await registry.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liberty-connectors", description=__doc__)
    parser.add_argument("--config", help="path to connectors.toml (default: from config/app.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list pools and connectors").set_defaults(func=_cmd_list)

    p_desc = sub.add_parser("describe", help="show one connector's definition")
    p_desc.add_argument("connector")
    p_desc.set_defaults(func=_cmd_describe)

    p_run = sub.add_parser("run", help="execute a query / call an endpoint")
    p_run.add_argument("connector")
    p_run.add_argument("name", help="query name (sql) or endpoint name (api)")
    p_run.add_argument("-p", "--param", action="append", metavar="KEY=VALUE", help="repeatable")
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except ConnectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
