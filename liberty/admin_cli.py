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
from liberty.main import ensure_plugins_on_sys_path


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #


class _Context:
    def __init__(self, args: argparse.Namespace) -> None:
        settings = load_settings(args.config_app) if args.config_app else load_settings()
        self.settings = settings
        # Make ``${LIBERTY_APPS_DIR}/../plugins/`` importable for the same reason the
        # uvicorn path does it in create_app(): any subcommand that ends up resolving
        # a job's python-step callable (``run-install-jobs``) needs ``nomasx1`` /
        # ``nomajde`` / etc. on sys.path. No-op when LIBERTY_APPS_DIR is unset.
        ensure_plugins_on_sys_path()
        connectors_path = args.config_connectors or settings.connectors.config_path
        # Pass master_key so the registry can decrypt ENC: password fields on pool
        # configs (e.g. the [pools.default] block init-db writes — encrypted with
        # this same key). Without it, the registry silently uses ENC:... as the
        # literal password and pg auth fails.
        self.registry = load_connectors(connectors_path, master_key=settings.crypto.master_key)
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


async def _seed_default_pool(ctx: _Context) -> bool:
    """If ``[pools.default]`` in connectors.toml has an empty url, write a working
    block. Picks the values based on what's in the environment:
      - POSTGRES_PASSWORD set → postgres url + password encrypted with LIBERTY_MASTER_KEY
      - POSTGRES_PASSWORD unset → sqlite (pipx / dev)
    Preserves operator-set values (does NOT overwrite a non-empty url).
    Returns True if it wrote anything (caller reloads the registry).
    """
    from pathlib import Path

    import tomlkit

    config_path = Path(ctx.settings.connectors.config_path)
    if not config_path.exists():
        return False

    # Check the RESOLVED url (env vars applied) rather than the raw string. An old
    # wheel may ship ``url = "${LIBERTY_DB_URL}"`` which is truthy in the file but
    # resolves to "" at runtime when the env var isn't set. We parse the file with
    # tomllib + substitute_env (the same path load_settings uses) so the seed
    # decision matches what the registry will actually see.
    import tomllib  # noqa: WPS433  — local import to keep module top tidy
    from liberty.config import substitute_env

    raw_doc = tomllib.loads(config_path.read_text())
    resolved = substitute_env(raw_doc)
    existing = (resolved.get("pools") or {}).get("default") or {}
    existing_url = (existing.get("url") or "").strip()
    if existing_url:
        # Stale-from-previous-install detection. ``--reset`` wipes the named volumes
        # but NOT the /apps bind mount (that's the host filesystem) — so a previous
        # install's [pools.default] survives, including its ENC: password encrypted
        # with the OLD master key. The new install's master_key can't decrypt it →
        # asyncpg gets the literal "ENC:..." as the password → InvalidPasswordError.
        # When we detect this case, overwrite with a fresh seed. An operator who
        # genuinely manages this pool by hand wouldn't be using an ENC: password
        # without a matching master key.
        existing_pw = (existing.get("password") or "").strip()
        master_key = ctx.settings.crypto.master_key
        if existing_pw.startswith("ENC:") and master_key:
            from liberty.crypto import CryptoError, decrypt
            try:
                decrypt(existing_pw, master_key)
            except CryptoError:
                print(
                    "[init-db] pools.default has an ENC: password that won't decrypt "
                    "with the current master_key (stale from a previous install on the "
                    "same /apps mount) — re-seeding.",
                    file=sys.stderr,
                )
                # Fall through to the seeding block below.
            else:
                return False
        else:
            return False

    doc = tomlkit.loads(config_path.read_text())
    pools = doc.get("pools") or tomlkit.table()

    new_table = tomlkit.table()
    pg_pw = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if pg_pw:
        master_key = ctx.settings.crypto.master_key
        if not master_key:
            print(
                "[init-db] POSTGRES_PASSWORD set but LIBERTY_MASTER_KEY missing — "
                "can't encrypt; pools.default left empty.",
                file=sys.stderr,
            )
            return False
        from liberty.crypto import encrypt
        pg_user = os.environ.get("POSTGRES_USER", "liberty")
        pg_host = os.environ.get("POSTGRES_HOST", "pg")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_db   = os.environ.get("POSTGRES_DB",   "liberty")
        new_table["url"] = f"postgresql+asyncpg://{pg_user}@{pg_host}:{pg_port}/{pg_db}"
        new_table["password"] = encrypt(pg_pw, master_key)
        new_table["dialect"] = "postgresql"
        new_table["pool_size"] = 5
    else:
        new_table["url"] = "sqlite+aiosqlite:///./liberty.db"
        new_table["pool_pre_ping"] = True

    if "pools" not in doc:
        doc["pools"] = pools
    pools["default"] = new_table
    config_path.write_text(tomlkit.dumps(doc))

    # Reload the registry so the rest of init-db (JobDatabase, etc.) sees the new pool.
    # MUST pass master_key — the newly-written pools.default carries an ENC: password
    # encrypted with this key; without it, the registry would feed the literal "ENC:..."
    # string to asyncpg and pg would reject auth.
    from liberty.connectors import load_connectors
    await ctx.registry.aclose()
    ctx.registry = load_connectors(config_path, master_key=ctx.settings.crypto.master_key)
    return True


async def _cmd_init_db(ctx: _Context, args: argparse.Namespace) -> int:
    seeded = await _seed_default_pool(ctx)
    if seeded:
        print(f"[init-db] seeded [pools.default] in {ctx.settings.connectors.config_path}")
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


async def _cmd_run_install_jobs(ctx: _Context, args: argparse.Namespace) -> int:
    """Fire every job with ``install_step`` set, in ascending step order.

    Used at the end of a fresh deployment to bootstrap the licensed apps (the
    ``install.sh full --apps WHEEL`` flow chains here after the stack is up).
    Idempotent: a job that already has a SUCCEEDED run is skipped (override
    with ``--force``). Bails on the first failure with the run's error message.
    """
    from sqlalchemy import select

    from liberty.jobs.models import JobRun
    from liberty.jobs.triggers import ManualTrigger
    from liberty.jobs.wiring import build_nomaflow, shutdown_nomaflow

    components = await build_nomaflow(ctx.settings, ctx.registry)
    if components is None:
        print(
            "nomaflow not configured — set [jobs] pool to an existing pool in app.toml",
            file=sys.stderr,
        )
        return 1

    try:
        install_jobs = [j for j in components.registry.jobs() if j.install_step is not None]
        install_jobs.sort(key=lambda j: (j.install_step, j.id))

        if not install_jobs:
            print("No install jobs (no jobs.toml entries have install_step set).")
            return 0

        print(f"Found {len(install_jobs)} install job(s):")
        for j in install_jobs:
            print(f"  step {j.install_step}: {j.id}")
        print()

        for job in install_jobs:
            if not args.force:
                async with components.db.session() as sess:
                    stmt = (
                        select(JobRun.id)
                        .where(JobRun.job_id == job.id, JobRun.state == "SUCCEEDED")
                        .limit(1)
                    )
                    if (await sess.execute(stmt)).scalar_one_or_none() is not None:
                        print(
                            f"[skip] {job.id} (step {job.install_step}) — already SUCCEEDED. "
                            "Use --force to re-run."
                        )
                        continue

            print(f"[run]  {job.id} (step {job.install_step}) …", flush=True)
            trigger = ManualTrigger(triggered_by="install")
            run = await components.runner.create_run(job, trigger)
            await components.runner.execute_run(job, trigger, run)

            async with components.db.session() as sess:
                final = await sess.get(JobRun, run.id)
            state = final.state if final else "UNKNOWN"
            if state != "SUCCEEDED":
                err = (final.error_message if final and final.error_message else "(no error message)")
                print(
                    f"[FAIL] {job.id} — state={state} — {err}\n"
                    f"       Fix the underlying issue, then re-run with: "
                    f"liberty-admin run-install-jobs",
                    file=sys.stderr,
                )
                return 1
            print(f"[done] {job.id} — SUCCEEDED")

        print("\nAll install jobs completed.")
        return 0
    finally:
        await shutdown_nomaflow(components)


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

    p_rij = sub.add_parser(
        "run-install-jobs",
        help="fire every job with install_step set, in order — used by install-apps.sh",
    )
    p_rij.add_argument(
        "--force",
        action="store_true",
        help="re-run jobs that already have a SUCCEEDED run (default: skip them)",
    )
    p_rij.set_defaults(func=_cmd_run_install_jobs)

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
