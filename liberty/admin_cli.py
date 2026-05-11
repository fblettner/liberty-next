"""``liberty-admin`` — manage the internal users / roles tables.

    liberty-admin init-db [--admin-username admin] [--password … | --password-env VAR]
    liberty-admin create-user <username> [--email …] [--full-name …]
                              [--password … | --password-env VAR] [--role R ...] [--superuser]
    liberty-admin set-password <username> [--password … | --password-env VAR]
    liberty-admin set-active <username> {--active | --inactive}
    liberty-admin list-users
    liberty-admin create-role <name> [--permission P ...] [--description …]

Reads ``config/app.toml`` for ``[auth] pool`` and ``config/connectors.toml`` for
the pool's connection URL (override with ``--config-app`` / ``--config-connectors``).
When no password is supplied, a random one is generated and printed once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from typing import Awaitable, Callable

from liberty.auth.db import AuthDatabase
from liberty.auth.service import AuthError, AuthService
from liberty.config import load_settings
from liberty.connectors import load_connectors

ADMIN_ROLE = "admin"


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #


class _Context:
    def __init__(self, args: argparse.Namespace) -> None:
        settings = load_settings(args.config_app) if args.config_app else load_settings()
        connectors_path = args.config_connectors or settings.connectors.config_path
        self.registry = load_connectors(connectors_path)
        self.auth_db = AuthDatabase(self.registry.pools, settings.auth.pool)

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
    await ctx.auth_db.create_schema()
    async with ctx.auth_db.session() as session:
        svc = AuthService(session)
        await svc.get_or_create_role(
            ADMIN_ROLE, permissions=["*"], description="Full access (wildcard)."
        )
        if await svc.count_users() > 0:
            _print({"schema": "ready", "admin_created": False})
            return 0
        password, generated = _resolve_password(args)
        user = await svc.create_user(
            args.admin_username,
            password=password,
            is_superuser=True,
            roles=[ADMIN_ROLE],
        )
        out = {"schema": "ready", "admin_created": True, "user": user.public_dict()}
        if generated:
            out["generated_password"] = password
        _print(out)
    return 0


async def _cmd_create_user(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    async with ctx.auth_db.session() as session:
        svc = AuthService(session)
        user = await svc.create_user(
            args.username,
            password=password,
            email=args.email,
            full_name=args.full_name,
            is_superuser=args.superuser,
            roles=args.role or None,
        )
        out = {"user": user.public_dict()}
        if generated:
            out["generated_password"] = password
        _print(out)
    return 0


async def _cmd_set_password(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    async with ctx.auth_db.session() as session:
        svc = AuthService(session)
        user = await svc.get_user_by_username(args.username)
        if user is None:
            raise SystemExit(f"unknown user {args.username!r}")
        await svc.set_password(user, password)
        out = {"user": user.public_dict(), "password_changed": True}
        if generated:
            out["generated_password"] = password
        _print(out)
    return 0


async def _cmd_set_active(ctx: _Context, args: argparse.Namespace) -> int:
    async with ctx.auth_db.session() as session:
        svc = AuthService(session)
        user = await svc.get_user_by_username(args.username)
        if user is None:
            raise SystemExit(f"unknown user {args.username!r}")
        await svc.set_active(user, args.active)
        _print({"user": user.public_dict()})
    return 0


async def _cmd_list_users(ctx: _Context, args: argparse.Namespace) -> int:
    async with ctx.auth_db.session() as session:
        users = await AuthService(session).list_users()
        _print([u.public_dict() for u in users])
    return 0


async def _cmd_create_role(ctx: _Context, args: argparse.Namespace) -> int:
    async with ctx.auth_db.session() as session:
        svc = AuthService(session)
        role = await svc.get_or_create_role(
            args.name, permissions=args.permission or [], description=args.description
        )
        _print({"role": {"name": role.name, "permissions": role.permissions, "description": role.description}})
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

    p_init = sub.add_parser("init-db", help="create the auth tables + bootstrap an admin")
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
