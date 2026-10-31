"""``liberty-admin`` — manage the internal users / roles (the TOML store or the DB tables).

    liberty-admin init-db [--admin-username admin] [--password … | --password-env VAR]
    liberty-admin create-user <username> [--email …] [--full-name …] [--attribute k=v ...]
                              [--password … | --password-env VAR] [--role R ...] [--superuser] [--must-change]
    liberty-admin set-password <username> [--password … | --password-env VAR] [--must-change]
    liberty-admin set-active <username> {--active | --inactive}
    liberty-admin set-attributes <username> [--attribute k=v ...]
    liberty-admin list-users
    liberty-admin create-role <name> [--permission P ...] [--description …] [--scope RULE ...]
    liberty-admin set-scope <name> {--rule RULE ... | --clear}
    liberty-admin list-roles
    liberty-admin run-install-jobs [--app X ...] [--force]
    liberty-admin migrate [--app X ...] [--dry-run] [--target-connector C] [--revision R] [--plugins-dir D] [--stamp]
    liberty-admin record-upgrade --kind upgrade|rollback [--fw-from V --fw-to V …] [--step name=status[:detail] …]
    liberty-admin set-default-pool        # rewrite [pools.default] from POSTGRES_HOST/_PORT/_USER/_PASSWORD/_DB
    liberty-admin retarget-pools --from pg:5432 --to db.example.com:5432   # every pool on that server

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
from pathlib import Path
from typing import Awaitable, Callable

from liberty.auth.authstore import ADMIN_ROLE, build_auth_backend
from liberty.auth.service import AuthError
from liberty.config import load_settings
from liberty.connectors import load_connectors
from liberty.licensing import verify_license
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
        self.connectors_path = Path(connectors_path)
        # Pass master_key so the registry can decrypt ENC: password fields on pool
        # configs (e.g. the [pools.default] block init-db writes — encrypted with
        # this same key). Without it, the registry silently uses ENC:... as the
        # literal password and pg auth fails. The license is verified like the web
        # lifespan does, else the licensed connectors (nomasx1 / nomajde) are dropped and
        # `migrate` / `run-install-jobs` can't see them.
        self.license = verify_license(settings.license.key, master_key=settings.crypto.master_key)
        self.registry = load_connectors(
            connectors_path, master_key=settings.crypto.master_key, license=self.license,
            pools_path=settings.connectors.pools_path,
        )
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


async def _seed_default_pool(ctx: _Context, *, force: bool = False) -> bool:
    """If ``[pools.default]`` in connectors.toml has an empty url, write a working
    block. Picks the values based on what's in the environment:
      - POSTGRES_PASSWORD set → postgres url + password encrypted with LIBERTY_MASTER_KEY
      - POSTGRES_PASSWORD unset → sqlite (pipx / dev)
    Preserves operator-set values (does NOT overwrite a non-empty url) — unless *force*
    (``set-default-pool``: the database moved, the environment says where).
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
    if existing_url and not force:
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
    from liberty.runtime import journal_schema

    journal_schema(ctx.settings, await ctx.backend.ready() or [])
    await ctx.backend.get_or_create_role(ADMIN_ROLE, permissions=["*"], description="Full access (wildcard).")
    # Create the nomaflow run-history tables on the same pool (idempotent — create_all
    # is a no-op if they exist). Done unconditionally because the tables are tiny and
    # creating them up-front avoids a "first job run errors because the table is missing"
    # foot-gun. `jobs.enabled = false` only gates the scheduler/runner, not the schema.
    from liberty.jobs.db import JobDatabase
    journal_schema(ctx.settings, await JobDatabase(ctx.registry.pools, ctx.settings.jobs.pool).create_schema())
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


def parse_attributes(items: list[str] | None) -> dict[str, str | list[str] | None]:
    """``KEY=VALUE`` pairs → attributes. ``KEY=a,b`` is a list, ``KEY=`` removes the key."""
    out: dict[str, str | list[str] | None] = {}
    for raw in items or []:
        key, sep, value = raw.partition("=")
        key = key.strip()
        if not key or not sep:
            raise ValueError(f"attribute {raw!r}: expected KEY=VALUE")
        value = value.strip()
        out[key] = None if value == "" else ([v.strip() for v in value.split(",")] if "," in value else value)
    return out


def parse_scope_rule(raw: str):
    """One data-scope rule from the command line — a TOML inline table (``{dd = "SY", values =
    ["00"], exclude = true}``) or a JSON object."""
    import json
    import tomllib

    from liberty.auth.scope import ScopeRule

    text = raw.strip()
    data = None
    try:
        data = tomllib.loads(f"r = {text}")["r"]
    except tomllib.TOMLDecodeError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"scope rule {raw!r}: neither a TOML inline table nor JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"scope rule {raw!r}: expected a table / object")
    try:
        return ScopeRule.model_validate(data)
    except ValueError as exc:
        raise ValueError(f"scope rule {raw!r}: {exc}") from exc


async def _cmd_create_user(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    user = await ctx.backend.create_user(
        args.username, password=password, email=args.email, full_name=args.full_name,
        is_superuser=args.superuser, roles=args.role or None, attributes=parse_attributes(args.attribute) or None,
        must_change_password=args.must_change,
    )
    out = {"user": user.public_dict()}
    if generated:
        out["generated_password"] = password
    _print(out)
    return 0


async def _cmd_set_password(ctx: _Context, args: argparse.Namespace) -> int:
    password, generated = _resolve_password(args)
    user = await ctx.backend.set_password(args.username, password, must_change=args.must_change)
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


async def _cmd_set_attributes(ctx: _Context, args: argparse.Namespace) -> int:
    user = await ctx.backend.set_attributes(args.username, parse_attributes(args.attribute), replace=args.replace)
    _print({"user": user.public_dict()})
    return 0


def _role_out(t) -> dict:
    name, perms, desc, scope = t
    return {"name": name, "permissions": perms, "description": desc, "scope": scope}


async def _cmd_list_roles(ctx: _Context, args: argparse.Namespace) -> int:
    _print([_role_out(t) for t in await ctx.backend.list_roles()])
    return 0


async def _cmd_set_scope(ctx: _Context, args: argparse.Namespace) -> int:
    if args.name not in {t[0] for t in await ctx.backend.list_roles()}:
        print(f"error: unknown role {args.name!r} (create-role first)", file=sys.stderr)
        return 2
    if not args.clear and not args.rule:
        print("error: give at least one --rule, or --clear to remove every rule", file=sys.stderr)
        return 2
    rules = [] if args.clear else [parse_scope_rule(r) for r in args.rule or []]
    t = await ctx.backend.get_or_create_role(args.name, scope=rules)
    _print({"role": _role_out(t)})
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
    scope = [parse_scope_rule(r) for r in args.scope] if args.scope else None
    # No --permission: a new role starts empty, an existing one keeps its permissions (None = untouched).
    t = await ctx.backend.get_or_create_role(
        args.name, permissions=args.permission if args.permission else None, description=args.description, scope=scope,
    )
    _print({"role": _role_out(t)})
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
# migrate — app schema migrations (plugins/<app>/db)
# --------------------------------------------------------------------------- #

MIGRATE_MODULE = "db"
"""Submodule each app plugin exposes its schema entry points from: ``plugins/<app>/db/__init__.py``."""


def _plugins_dir(explicit: str | None) -> Path:
    """Same resolution as :func:`liberty.main.ensure_plugins_on_sys_path`: an explicit
    ``--plugins-dir``, else ``${LIBERTY_APPS_DIR}/../plugins``, else ``./plugins``."""
    if explicit:
        return Path(explicit).resolve()
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    return (Path(apps).parent / "plugins" if apps else Path("plugins")).resolve()


def discover_migratable_apps(plugins_dir: Path | None) -> list[str]:
    """The app packages under *plugins_dir* that ship a ``db`` module — the only ones
    ``migrate`` knows how to drive. Sorted so the run order is stable."""
    if plugins_dir is None or not plugins_dir.is_dir():
        return []
    return sorted(
        d.name for d in plugins_dir.iterdir()
        if d.is_dir() and (d / "__init__.py").is_file() and (d / MIGRATE_MODULE / "__init__.py").is_file()
    )


async def _migrate_one(ctx: _Context, app: str, *, target: str, revision: str, dry_run: bool,
                       stamp: str | None = None) -> dict:
    """Drive one app's ``db`` module. Contract (duck-typed, what nomasx1.db already exposes):

    * ``upgrade_schema(*, connectors, target_connector, revision="head")`` — async, applies.
    * ``pending_revisions(*, connectors, target_connector)`` — optional (sync or async),
      returns the revision ids not yet applied; without it a dry run can only say "unknown".
    * ``DEFAULT_TARGET_CONNECTOR`` — optional; the connector the schema lives on when the
      operator doesn't pass ``--target-connector`` (defaults to the app's own name).
    * ``stamp_revision(*, connectors, target_connector, revision)`` — optional; sets the version
      table without running anything (``--stamp REV``: the repair when the version is ahead of
      the real schema). ``--dry-run`` after it shows what became pending.
    """
    import importlib
    import inspect

    mod = importlib.import_module(f"{app}.{MIGRATE_MODULE}")
    upgrade = getattr(mod, "upgrade_schema", None)
    if upgrade is None:
        return {"app": app, "status": "skipped", "reason": f"{app}.{MIGRATE_MODULE} has no upgrade_schema()"}
    target = target or getattr(mod, "DEFAULT_TARGET_CONNECTOR", None) or app
    if target not in ctx.registry.names() and target not in ctx.registry.pools.names():
        return {
            "app": app, "status": "error", "target_connector": target,
            "reason": f"no connector or pool named {target!r} (the plugin resolves its database through "
                      f"connectors.pools.engine(target_connector)) — pass --target-connector",
        }
    out: dict = {"app": app, "target_connector": target, "revision": revision}

    if stamp:
        stamp_fn = getattr(mod, "stamp_revision", None)
        if stamp_fn is None:
            return {"app": app, "status": "error", "target_connector": target,
                    "reason": f"{app}.{MIGRATE_MODULE} has no stamp_revision() — stamp with alembic directly"}
        res = stamp_fn(connectors=ctx.registry, target_connector=target, revision=stamp)
        if inspect.isawaitable(res):
            await res
        out["stamped"] = stamp

    pending_fn = getattr(mod, "pending_revisions", None)
    if pending_fn is not None:
        res = pending_fn(connectors=ctx.registry, target_connector=target)
        pending = list(await res) if inspect.isawaitable(res) else list(res)
        out["pending"] = pending
    else:
        out["pending"] = None  # the plugin can't say; a run is still safe (alembic is idempotent)

    if dry_run:
        out["status"] = "pending" if out["pending"] else ("up-to-date" if out["pending"] == [] else "unknown")
        return out
    if out["pending"] == []:
        out["status"] = "up-to-date"
        return out
    res = upgrade(connectors=ctx.registry, target_connector=target, revision=revision)
    if inspect.isawaitable(res):
        res = await res
    out["status"] = "applied"
    out["result"] = res
    _record_schema_upgrade(ctx, app, out)
    return out


def _record_schema_upgrade(ctx: _Context, app: str, out: dict) -> None:
    """Journal the applied revisions in the upgrade history (Liberty Next → Deploy → History → Upgrades),
    next to the framework / apps version bumps. Best-effort: the migration already ran."""
    try:
        from liberty.versioning import ConfigVersionStore

        store = ConfigVersionStore(ctx.connectors_path.parent)
        pending = out.get("pending") or []
        store.record_upgrade(
            component=f"{app}-schema", kind="upgrade",
            from_version=store.current_app_version(f"{app}-schema"),
            to_version=pending[-1] if pending else out["revision"],
            summary=f"liberty-admin migrate: {len(pending) or '?'} revision(s) on {out['target_connector']}",
            who="liberty-admin",
        )
    except Exception as exc:  # noqa: BLE001 — journaling must never fail the migration
        print(f"warning: could not record the upgrade in history: {exc}", file=sys.stderr)


async def _cmd_migrate(ctx: _Context, args: argparse.Namespace) -> int:
    """Apply (or, with ``--dry-run``, list) every app's pending schema migrations.

    Replaces the manual "run the nomasx1-upgrade-schema-1 job from the UI" step: the
    upgrade path (``liberty upgrade`` / the entrypoint) can call this and know, from the
    exit code, whether the schema is in step with the code. Exit 0 = nothing pending or
    everything applied; 1 = an app failed / is misconfigured; 2 = ``--dry-run`` found
    pending revisions (so a pre-flight can gate on it).
    """
    plugins_dir = _plugins_dir(args.plugins_dir)
    if args.plugins_dir and str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))
    apps = discover_migratable_apps(plugins_dir)
    wanted = list(args.app or [])
    unknown = [a for a in wanted if a not in apps]
    if unknown:
        print(f"error: no migratable plugin for {', '.join(unknown)} (found: {', '.join(apps) or 'none'} "
              f"under {plugins_dir})", file=sys.stderr)
        return 1
    if wanted:
        apps = wanted
    if args.stamp and len(wanted) != 1:  # never stamp implicitly: name the app
        print("error: --stamp needs exactly one explicit --app X", file=sys.stderr)
        return 1
    results = []
    for app in apps:
        try:
            results.append(await _migrate_one(
                ctx, app, target=args.target_connector or "", revision=args.revision, dry_run=args.dry_run,
                stamp=args.stamp,
            ))
        except Exception as exc:  # noqa: BLE001 — report per app, keep going
            results.append({"app": app, "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
    pending_total = sum(len(r.get("pending") or []) for r in results)
    errors = [r for r in results if r["status"] == "error"]
    _print({
        "plugins_dir": str(plugins_dir), "dry_run": args.dry_run, "apps": results,
        "pending_total": pending_total, "errors": len(errors),
    })
    if errors:
        return 1
    if args.dry_run and pending_total:
        return 2
    return 0



# --------------------------------------------------------------------------- #
# record-upgrade — the report `liberty upgrade` / `liberty rollback` hand in
# --------------------------------------------------------------------------- #

_STEP_MARK = {"ok": "✔", "warn": "!", "fail": "✗", "skipped": "–"}


def _parse_step(raw: str) -> dict:
    """``name=status[:detail]`` → ``{name, status, detail}``; status ∈ ok | warn | fail | skipped."""
    name, _, rest = raw.partition("=")
    status, _, detail = rest.partition(":")
    status = (status or "ok").strip().lower()
    if status not in _STEP_MARK:
        raise argparse.ArgumentTypeError(f"step {name!r}: status must be one of {', '.join(_STEP_MARK)}")
    return {"name": name.strip(), "status": status, "detail": detail.strip() or None}


def render_upgrade_report(data: dict) -> str:
    """The Markdown Liberty Next → Deploy → History → Upgrades shows for a report."""
    kind = data.get("kind", "upgrade")
    fw, apps, tag = data.get("framework", {}), data.get("apps", {}), data.get("image_tag", {})

    def arrow(d: dict) -> str:
        a, b = d.get("from") or "-", d.get("to") or "-"
        return f"{a} → {b}" if a != b else b

    lines = [f"# {kind.capitalize()} — {data.get('finished_at', '')[:19].replace('T', ' ')} UTC", ""]
    lines += ["| | |", "|---|---|",
              f"| Framework | {arrow(fw)} |",
              f"| Apps | {arrow(apps) if (apps.get('from') or apps.get('to')) else 'not installed'} |",
              f"| Image tag | {arrow(tag)} |",
              f"| Layout / chain | {data.get('layout') or '?'} — `{data.get('chain') or '?'}` |",
              f"| Backup | {('`' + data['backup'] + '`') if data.get('backup') else 'none'} |",
              f"| Health after | {data.get('health') or '?'} |",
              f"| Host | {data.get('host') or '?'} |", ""]
    steps = data.get("steps") or []
    if steps:
        lines += ["## Steps", ""]
        for st in steps:
            mark = _STEP_MARK.get(st.get("status", "ok"), "?")
            detail = f" — {st['detail']}" if st.get("detail") else ""
            lines.append(f"- {mark} **{st['name']}**{detail}")
        lines.append("")
    mig = data.get("migrations")
    if isinstance(mig, dict):
        lines += ["## Schema migrations", ""]
        apps_rows = mig.get("apps") or []
        if not apps_rows:
            lines.append("No migratable app plugin.")
        for a in apps_rows:
            pend = a.get("pending")
            what = (f"{len(pend)} revision(s): " + ", ".join(pend)) if pend else ("up to date" if pend == [] else "")
            reason = f" — {a['reason']}" if a.get("reason") else ""
            lines.append(f"- **{a.get('app')}** on `{a.get('target_connector', '?')}`: {a.get('status')}{(' — ' + what) if what else ''}{reason}")
        lines.append("")
    doc = data.get("doctor") or {}
    if doc.get("text"):
        n = doc.get("problems")
        lines += [f"## Doctor ({n} problem(s))" if n is not None else "## Doctor", "", "```", doc["text"].rstrip(), "```", ""]
    return "\n".join(lines)


async def _cmd_record_upgrade(ctx: _Context, args: argparse.Namespace) -> int:
    """Store the report of an upgrade / rollback run by ``release/liberty`` and journal it as a
    ``stack`` row of the upgrade history (next to the framework / apps version bumps the server
    records at startup). The doctor output is read from stdin when ``--doctor-text -`` is given."""
    import json
    import socket
    from datetime import datetime, timezone


    if args.from_json:
        # Re-journal a report saved earlier (the rollback keeps the upgrade's report across the
        # volume restore this way). The JSON is taken as-is; nothing else on the command line is read.
        raw = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"error: --from-json is not JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(data, dict) or data.get("kind") not in ("upgrade", "rollback", "install"):
            print("error: --from-json: expected a report object with a kind", file=sys.stderr)
            return 2
        return _journal_report(ctx, data, who=args.who)
    if not args.kind:
        print("error: --kind is required (unless --from-json)", file=sys.stderr)
        return 2
    doctor_text = None
    if args.doctor_text == "-":
        doctor_text = sys.stdin.read()
    elif args.doctor_text:
        doctor_text = Path(args.doctor_text).read_text(encoding="utf-8")
    migrations = None
    if args.migrations:
        try:
            migrations = json.loads(args.migrations)
        except json.JSONDecodeError as exc:
            print(f"error: --migrations is not JSON: {exc}", file=sys.stderr)
            return 2
    now = datetime.now(timezone.utc)
    data = {
        "kind": args.kind,
        "started_at": args.started_at or now.isoformat(timespec="seconds"),
        "finished_at": now.isoformat(timespec="seconds"),
        "host": args.host or socket.gethostname(),
        "layout": args.layout, "chain": args.chain,
        "image_tag": {"from": args.tag_from, "to": args.tag_to},
        "framework": {"from": args.fw_from, "to": args.fw_to},
        "apps": {"from": args.apps_from, "to": args.apps_to},
        "backup": args.backup, "health": args.health,
        "doctor": {"problems": args.doctor_problems, "text": doctor_text},
        "migrations": migrations,
        "steps": list(args.step or []),
    }
    return _journal_report(ctx, data, who=args.who)


def _journal_report(ctx: _Context, data: dict, *, who: str) -> int:
    """Write the report files and the ``stack`` history row for *data*; prints where they went."""
    from datetime import datetime

    from liberty.versioning import ConfigVersionStore

    kind = data["kind"]
    fw, apps, tag = data.get("framework") or {}, data.get("apps") or {}, data.get("image_tag") or {}
    steps = data.get("steps") or []
    failed = [st["name"] for st in steps if st.get("status") == "fail"]
    finished = data.get("finished_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = finished[:19].replace("-", "").replace(":", "").replace("T", "-")
    store = ConfigVersionStore(ctx.connectors_path.parent)
    name = f"{stamp}-{kind}"
    jp, mp = store.write_upgrade_report(name, data, render_upgrade_report(data))
    summary = f"{kind}: framework {fw.get('from') or '-'} → {fw.get('to') or '-'}"
    if apps.get("to"):
        summary += f", apps {apps.get('from') or '-'} → {apps['to']}"
    if failed:
        summary += f"; FAILED: {', '.join(failed)}"
    row = store.record_upgrade(
        component="stack", kind=kind, from_version=fw.get("from"), to_version=fw.get("to") or tag.get("to") or "?",
        summary=summary, who=who, report=name, created_at=finished,
    )
    _print({"id": row["id"], "report": name, "json_path": str(jp), "markdown_path": str(mp), "failed_steps": failed})
    return 0


def _add_password_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--password", help="set this password (otherwise a random one is generated)")
    p.add_argument("--password-env", metavar="VAR", help="read the password from this env var")


async def _cmd_set_default_pool(ctx: _Context, args: argparse.Namespace) -> int:
    """Rewrite ``[pools.default]`` from the environment (``POSTGRES_HOST`` / ``_PORT`` / ``_USER`` /
    ``_PASSWORD`` / ``_DB``; the password encrypted with the master key) — the framework database
    moved to another server (``./liberty db external``) or came back (``./liberty db embedded``)."""
    if not os.environ.get("POSTGRES_PASSWORD", "").strip():
        if not os.environ.get("LIBERTY_DB_URL", "").strip():
            raise SystemExit("POSTGRES_PASSWORD is not set — nothing to point [pools.default] at")
        # The light layout's own database (SQLite on its volume): follow LIBERTY_DB_URL again.
        import tomlkit
        doc = tomlkit.parse(Path(ctx.settings.connectors.config_path).read_text(encoding="utf-8"))
        if "pools" not in doc:
            doc["pools"] = tomlkit.table()
        doc["pools"]["default"] = {"url": "${LIBERTY_DB_URL}", "pool_pre_ping": True}
        Path(ctx.settings.connectors.config_path).write_text(tomlkit.dumps(doc), encoding="utf-8")
        _print({"pool": "default", "url": "${LIBERTY_DB_URL}", "file": str(ctx.settings.connectors.config_path)})
        return 0
    if not await _seed_default_pool(ctx, force=True):
        raise SystemExit("[pools.default] not written (see the message above)")
    host = os.environ.get("POSTGRES_HOST", "pg")
    _print({"pool": "default", "host": host, "port": os.environ.get("POSTGRES_PORT", "5432"),
            "database": os.environ.get("POSTGRES_DB", "liberty"), "file": str(ctx.connectors_path)})
    return 0


_HOST_RE = r"@(\[[^\]]+\]|[^:/@?]+)(?::(\d+))?(?=[/?]|$)"


def _retarget_url(url: str, old_host: str, old_port: str, new_host: str, new_port: str) -> str | None:
    """*url* with its ``@host[:port]`` replaced when it names *old_host* (and *old_port*, the
    default port when the url has none); ``None`` when it points elsewhere. A ``${VAR}`` host is
    left alone (it already follows the environment)."""
    import re
    m = re.search(_HOST_RE, url)
    if m is None or m.group(1) != old_host or (m.group(2) or "5432") != old_port:
        return None
    return url[:m.start()] + f"@{new_host}:{new_port}" + url[m.end():]


async def _cmd_retarget_pools(ctx: _Context, args: argparse.Namespace) -> int:
    """Point every pool whose url names ``--from host[:port]`` at ``--to host[:port]`` (the apps'
    pools — nomasx1, nomajde… — live on the same server as the framework database)."""
    import tomlkit
    old_host, _, old_port = args.from_server.partition(":")
    new_host, _, new_port = args.to_server.partition(":")
    old_port, new_port = old_port or "5432", new_port or "5432"
    doc = tomlkit.parse(ctx.connectors_path.read_text(encoding="utf-8"))
    changed: list[dict[str, str]] = []
    for name, pool in (doc.get("pools") or {}).items():
        url = str(pool.get("url") or "")
        new = _retarget_url(url, old_host, old_port, new_host, new_port)
        if new is not None and new != url:
            pool["url"] = new
            changed.append({"pool": str(name), "from": url, "to": new})
    if changed and not args.dry_run:
        ctx.connectors_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    _print({"changed": changed, "dry_run": bool(args.dry_run), "file": str(ctx.connectors_path)})
    return 0


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
    p_cu.add_argument("--attribute", action="append", metavar="KEY=VALUE", help="data-scope attribute (repeatable; KEY=a,b for a list)")
    p_cu.add_argument("--superuser", action="store_true")
    p_cu.add_argument("--must-change", action="store_true", help="ask for a new password at the first sign-in")
    _add_password_args(p_cu)
    p_cu.set_defaults(func=_cmd_create_user)

    p_sp = sub.add_parser("set-password", help="set a user's password")
    p_sp.add_argument("username")
    _add_password_args(p_sp)
    p_sp.add_argument("--must-change", action="store_true", help="a temporary password: a new one is asked at the next sign-in")
    p_sp.set_defaults(func=_cmd_set_password)

    p_sa = sub.add_parser("set-active", help="enable/disable a user")
    p_sa.add_argument("username")
    g = p_sa.add_mutually_exclusive_group(required=True)
    g.add_argument("--active", dest="active", action="store_true")
    g.add_argument("--inactive", dest="active", action="store_false")
    p_sa.set_defaults(func=_cmd_set_active)

    sub.add_parser("list-users", help="list all users").set_defaults(func=_cmd_list_users)

    p_sat = sub.add_parser("set-attributes", help="set a user's data-scope attributes (company, pole, …)")
    p_sat.add_argument("username")
    p_sat.add_argument("attribute", nargs="+", metavar="KEY=VALUE", help="KEY=a,b for a list; KEY= removes the key")
    p_sat.add_argument("--replace", action="store_true", help="replace every attribute instead of merging")
    p_sat.set_defaults(func=_cmd_set_attributes)

    p_cr = sub.add_parser("create-role", help="create or update a role")
    p_cr.add_argument("name")
    p_cr.add_argument("--permission", action="append", metavar="PERM", help="repeatable")
    p_cr.add_argument("--description")
    p_cr.add_argument(
        "--scope", action="append", metavar="RULE",
        help='data-scope rule, repeatable — a TOML inline table: \'{dd = "CO", source = "#USER.company#", mode = "deny"}\' '
             '(replaces the role\'s rules when given)',
    )
    p_cr.set_defaults(func=_cmd_create_role)

    sub.add_parser("list-roles", help="list roles with their permissions and data scope").set_defaults(func=_cmd_list_roles)

    p_ss = sub.add_parser("set-scope", help="replace a role's data-scope rules")
    p_ss.add_argument("name")
    p_ss.add_argument("--rule", action="append", metavar="RULE", help='TOML inline table or JSON, repeatable: \'{dd = "SY", values = ["00"], exclude = true}\'')
    p_ss.add_argument("--clear", action="store_true", help="remove every rule (the role grants every row again)")
    p_ss.set_defaults(func=_cmd_set_scope)

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

    p_mig = sub.add_parser(
        "migrate",
        help="apply each app plugin's pending schema migrations (plugins/<app>/db) — used by `liberty upgrade`",
    )
    p_mig.add_argument("--app", action="append", help="only this app (repeatable; default: every migratable plugin)")
    p_mig.add_argument("--dry-run", action="store_true", help="list pending revisions, apply nothing (exit 2 when some are pending)")
    p_mig.add_argument("--target-connector", help="the connector the schema lives on (default: the plugin's DEFAULT_TARGET_CONNECTOR, else the app name)")
    p_mig.add_argument("--revision", default="head", help="alembic target revision (default: head)")
    p_mig.add_argument("--plugins-dir", help="where the app plugins live (default: ${LIBERTY_APPS_DIR}/../plugins)")
    p_mig.add_argument("--stamp", metavar="REV", help="set the app's alembic version to REV without running anything (repair: the version is ahead of the real schema), then apply what is pending unless --dry-run; needs --app")
    p_mig.set_defaults(func=_cmd_migrate)

    p_rec = sub.add_parser(
        "record-upgrade",
        help="store an upgrade / rollback report (written by `liberty upgrade`) in the upgrade history",
    )
    p_rec.add_argument("--kind", choices=["upgrade", "rollback", "install"], help="required unless --from-json")
    p_rec.add_argument("--from-json", help="re-journal a saved report (its .json file, or '-' for stdin) as-is")
    p_rec.add_argument("--who", default="liberty")
    p_rec.add_argument("--host")
    p_rec.add_argument("--started-at", help="ISO timestamp of the run's start (default: now)")
    p_rec.add_argument("--layout")
    p_rec.add_argument("--chain", help="the COMPOSE_FILE chain in force")
    p_rec.add_argument("--tag-from"); p_rec.add_argument("--tag-to")
    p_rec.add_argument("--fw-from"); p_rec.add_argument("--fw-to")
    p_rec.add_argument("--apps-from"); p_rec.add_argument("--apps-to")
    p_rec.add_argument("--backup", help="the backup directory taken before the run")
    p_rec.add_argument("--health", help="the container's health status at the end")
    p_rec.add_argument("--doctor-problems", type=int)
    p_rec.add_argument("--doctor-text", help="file with doctor's output, or '-' for stdin")
    p_rec.add_argument("--migrations", help="the JSON printed by `liberty-admin migrate`")
    p_rec.add_argument("--step", action="append", type=_parse_step, help="name=status[:detail] (status: ok|warn|fail|skipped); repeatable, in order")
    p_rec.set_defaults(func=_cmd_record_upgrade)

    sub.add_parser(
        "set-default-pool",
        help="rewrite [pools.default] from POSTGRES_HOST / _PORT / _USER / _PASSWORD / _DB (the database moved)",
    ).set_defaults(func=_cmd_set_default_pool)

    p_rt = sub.add_parser("retarget-pools", help="point every pool on one database server at another")
    p_rt.add_argument("--from", dest="from_server", required=True, help="host[:port] the pools name today (e.g. pg:5432)")
    p_rt.add_argument("--to", dest="to_server", required=True, help="host[:port] they move to")
    p_rt.add_argument("--dry-run", action="store_true", help="list the pools without writing")
    p_rt.set_defaults(func=_cmd_retarget_pools)

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
    except (AuthError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
