"""``liberty-admin`` — manage the internal users / roles (the TOML store or the DB tables).

    liberty-admin init-db [--admin-username admin] [--password … | --password-env VAR]
    liberty-admin create-user <username> [--email …] [--full-name …]
                              [--password … | --password-env VAR] [--role R ...] [--superuser]
    liberty-admin set-password <username> [--password … | --password-env VAR]
    liberty-admin set-active <username> {--active | --inactive}
    liberty-admin list-users
    liberty-admin create-role <name> [--permission P ...] [--description …]

Operates on whatever ``[auth] backend`` is configured in ``config/app.toml``: ``"toml"`` (the
default) edits ``[auth] toml_path`` (``config/auth.toml``) — no database; ``"db"`` uses the
``ly2_*`` tables on ``[auth] pool`` (``init-db`` then creates them). Override the config paths with
``--config-app`` / ``--config-connectors``. When no password is supplied, a random one is generated
and printed once. (``init-db`` is the name kept for `./start.sh init-db`; for the TOML backend it
just creates ``auth.toml`` + bootstraps an ``admin``.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from typing import Awaitable, Callable

from liberty.auth.authstore import ADMIN_ROLE, build_auth_backend
from liberty.auth.service import AuthError
from liberty.config import load_settings
from liberty.connectors import load_connectors


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #


class _Context:
    def __init__(self, args: argparse.Namespace) -> None:
        settings = load_settings(args.config_app) if args.config_app else load_settings()
        self.settings = settings
        connectors_path = args.config_connectors or settings.connectors.config_path
        self.registry = load_connectors(connectors_path)  # for the DB backend's pool; harmless for TOML
        self.backend = build_auth_backend(settings, self.registry.pools)

    async def aclose(self) -> None:
        await self.registry.aclose()


def _resolve_password(args: argparse.Namespace) -> tuple[str, bool]:
    if getattr(args, "password", None):
        return args.password, False
    env_var = getattr(args, "password_env", None)
    if env_var:
        value = os.environ.get(env_var)
        if not value:
            raise SystemExit(f"environment variable {env_var!r} is empty or unset")
        return value, False
    return secrets.token_urlsafe(15), True


def _print(obj) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


async def _cmd_init_db(ctx: _Context, args: argparse.Namespace) -> int:
    await ctx.backend.ready()
    await ctx.backend.get_or_create_role(ADMIN_ROLE, permissions=["*"], description="Full access (wildcard).")
    # Create the nomaflow run-history tables on the same pool (idempotent — create_all
    # is a no-op if they exist). Done unconditionally because the tables are tiny and
    # creating them up-front avoids a "first job run errors because the table is missing"
    # foot-gun. `jobs.enabled = false` only gates the scheduler/runner, not the schema.
    from liberty.jobs.db import JobDatabase
    await JobDatabase(ctx.registry.pools, ctx.settings.jobs.pool).create_schema()
    base = {"backend": ctx.settings.auth.backend, "ready": True, "nomaflow_schema": "ready"}
    if await ctx.backend.count_users() > 0:
        _print({**base, "admin_created": False})
        return 0
    password, generated = _resolve_password(args)
    user = await ctx.backend.create_user(
        args.admin_username, password=password, is_superuser=True, roles=[ADMIN_ROLE]
    )
    out = {**base, "admin_created": True, "user": user.public_dict()}
    if generated:
        out["generated_password"] = password
    _print(out)
    return 0


async def _cmd_create_user(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    user = await ctx.backend.create_user(
        args.username, password=password, email=args.email, full_name=args.full_name,
        is_superuser=args.superuser, roles=args.role or None,
    )
    out = {"user": user.public_dict()}
    if generated:
        out["generated_password"] = password
    _print(out)
    return 0


async def _cmd_set_password(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    user = await ctx.backend.set_password(args.username, password)
    out = {"user": user.public_dict(), "password_changed": True}
    if generated:
        out["generated_password"] = password
    _print(out)
    return 0


async def _cmd_set_active(ctx: _Context, args: argparse.Namespace) -> int:
    _print({"user": (await ctx.backend.set_active(args.username, args.active)).public_dict()})
    return 0


async def _cmd_list_users(ctx: _Context, args: argparse.Namespace) -> int:
    _print([u.public_dict() for u in await ctx.backend.list_users()])
    return 0


async def _cmd_create_role(ctx: _Context, args: argparse.Namespace) -> int:
    name, perms, desc = await ctx.backend.get_or_create_role(
        args.name, permissions=args.permission or [], description=args.description
    )
    _print({"role": {"name": name, "permissions": perms, "description": desc}})
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def _add_password_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--password", help="set this password (otherwise a random one is generated)")
    p.add_argument("--password-env", metavar="VAR", help="read the password from this env var")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liberty-admin", description=__doc__)
    parser.add_argument("--config-app", help="path to app.toml (default: config/app.toml)")
    parser.add_argument("--config-connectors", help="path to connectors.toml (default: from app.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create the store (DB tables or auth.toml) + bootstrap an admin")
    p_init.add_argument("--admin-username", default="admin")
    _add_password_args(p_init)
    p_init.set_defaults(func=_cmd_init_db)

    p_cu = sub.add_parser("create-user", help="create a local user")
    p_cu.add_argument("username")
    p_cu.add_argument("--email")
    p_cu.add_argument("--full-name")
    p_cu.add_argument("--role", action="append", metavar="ROLE", help="repeatable")
    p_cu.add_argument("--superuser", action="store_true")
    _add_password_args(p_cu)
    p_cu.set_defaults(func=_cmd_create_user)

    p_sp = sub.add_parser("set-password", help="set a user's password")
    p_sp.add_argument("username")
    _add_password_args(p_sp)
    p_sp.set_defaults(func=_cmd_set_password)

    p_sa = sub.add_parser("set-active", help="enable/disable a user")
    p_sa.add_argument("username")
    g = p_sa.add_mutually_exclusive_group(required=True)
    g.add_argument("--active", dest="active", action="store_true")
    g.add_argument("--inactive", dest="active", action="store_false")
    p_sa.set_defaults(func=_cmd_set_active)

    sub.add_parser("list-users", help="list all users").set_defaults(func=_cmd_list_users)

    p_cr = sub.add_parser("create-role", help="create or update a role")
    p_cr.add_argument("name")
    p_cr.add_argument("--permission", action="append", metavar="PERM", help="repeatable")
    p_cr.add_argument("--description")
    p_cr.set_defaults(func=_cmd_create_role)

    return parser


async def _run(args: argparse.Namespace) -> int:
    ctx = _Context(args)
    try:
        fn: Callable[[_Context, argparse.Namespace], Awaitable[int]] = args.func
        return await fn(ctx, args)
    finally:
        await ctx.aclose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
