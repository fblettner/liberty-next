"""``liberty-ops`` — the one operator command for a **Python** install (pipx or a venv, with or
without systemd). The Docker twin is ``release/liberty``; both have the same verbs:

    liberty-ops status                       versions, config dir, service, /ready, pending migrations, last upgrade
    liberty-ops doctor                       what's wrong and how to fix it (exit 1 when something is)
    liberty-ops start | stop | restart       the systemd unit (LIBERTY_SERVICE, default liberty-next)
    liberty-ops logs [-f]                    journalctl for that unit
    liberty-ops backup [--dest DIR] [--keep DAYS]      config dir (+ .versions), apps dir, SQLite file, pg_dump
    liberty-ops restore <dir> [--yes]        the reverse (stops / starts the service around it)
    liberty-ops migrate [--dry-run]          = liberty-admin migrate
    liberty-ops upgrade [--version X] [--apps WHEEL] [--keep DAYS] [--no-restart]
                                             doctor → backup gate → pipx upgrade / pip install → apps wheel →
                                             migrations → restart → health → report → status
    liberty-ops rollback [--yes] [--apps WHEEL]        previous framework version + the pre-upgrade backup

Design rules, same as the Docker script: the backup is the only hard gate of an upgrade; doctor is
advisory there (an upgrade is often the fix); every run leaves a report in Settings → History →
Upgrades (``liberty-admin record-upgrade``); what a rollback needs is recorded in
``<config>/.versions/ops-state.json``.

The post-upgrade steps run the **new** code through subprocesses (``liberty-admin …`` from the
same bin directory) — this process keeps running the old version in memory while pipx swaps
the venv underneath it, so it must not import anything new after the install step.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

FRAMEWORK_DIST = "liberty-next"
APPS_DIST = "liberty-apps"
DEFAULT_SERVICE = "liberty-next"
STATE_FILE = "ops-state.json"  # under <config>/.versions/

# ── output ────────────────────────────────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()
_C = {k: (v if _TTY else "") for k, v in {
    "red": "\033[0;31m", "green": "\033[0;32m", "yellow": "\033[0;33m", "blue": "\033[0;34m",
    "bold": "\033[1m", "nc": "\033[0m",
}.items()}


def info(msg: str) -> None:
    print(f"{_C['blue']}▸{_C['nc']} {msg}")


def ok(msg: str) -> None:
    print(f"{_C['green']}✔{_C['nc']} {msg}")


def warn(msg: str) -> None:
    print(f"{_C['yellow']}!{_C['nc']} {msg}")


def fail_line(msg: str, fix: str) -> None:
    print(f"{_C['red']}✗{_C['nc']} {msg}\n   → {fix}")


class OpsError(Exception):
    """A fatal, already-explained condition — ``main`` prints it and exits 1."""


# ── environment ───────────────────────────────────────────────────────────────────────────
Runner = Callable[..., subprocess.CompletedProcess]


def run(cmd: list[str], *, check: bool = False, capture: bool = True, env: dict[str, str] | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    """The one subprocess seam (tests swap it)."""
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, input=stdin,
                          env={**os.environ, **(env or {})})


@dataclass
class Env:
    """Where this install lives — resolved once per command from the settings + the environment."""

    settings: object
    config_dir: Path            # the dir holding connectors.toml (and .versions/)
    app_config: Path            # app.toml
    apps_dir: Path | None       # LIBERTY_APPS_DIR (= <apps>/config) or None
    service: str
    bin_dir: Path               # where liberty-admin / liberty-next live (next to this interpreter's script)
    port: int
    runner: Runner = run

    @property
    def versions_dir(self) -> Path:
        return self.config_dir / ".versions"

    @property
    def state_path(self) -> Path:
        return self.versions_dir / STATE_FILE

    def tool(self, name: str) -> str:
        p = self.bin_dir / name
        return str(p) if p.exists() else name

    # state the rollback reads
    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, **changes: object) -> dict:
        st = self.load_state()
        st.update(changes)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
        return st


def resolve_env(args: argparse.Namespace, *, runner: Runner = run) -> Env:
    from liberty.config import load_settings, resolve_app_config_path

    app_config = Path(args.config_app) if getattr(args, "config_app", None) else resolve_app_config_path()
    if app_config.exists():
        settings = load_settings(app_config)
    else:  # no app.toml: the framework runs on defaults, so does this tool
        from liberty.config import Settings

        settings = Settings()
    apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
    return Env(
        settings=settings,
        config_dir=Path(settings.connectors.config_path).resolve().parent,
        app_config=app_config.resolve(),
        apps_dir=Path(apps) if apps else None,
        service=os.environ.get("LIBERTY_SERVICE", DEFAULT_SERVICE),
        bin_dir=Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path(sys.executable).parent,
        port=int(getattr(getattr(settings, "app", None), "port", 8000) or 8000),
        runner=runner,
    )


# ── probes ────────────────────────────────────────────────────────────────────────────────
def dist_version(name: str, config_dir: Path | None = None) -> str | None:
    try:
        return _dist_version(name)
    except PackageNotFoundError:
        if name == APPS_DIST and config_dir is not None:  # the materialised bundle's manifest
            from liberty.config_merge import bundled_version

            return bundled_version(config_dir)
        return None


def installer(env: Env) -> str:
    """``pipx`` when the framework is a pipx-managed venv, else ``pip`` (plain venv)."""
    if shutil.which("pipx"):
        r = env.runner(["pipx", "list", "--json"])
        if r.returncode == 0:
            try:
                venvs = json.loads(r.stdout).get("venvs", {})
            except json.JSONDecodeError:
                venvs = {}
            if FRAMEWORK_DIST in venvs:
                return "pipx"
    return "pip"


def systemd_available(env: Env) -> bool:
    return shutil.which("systemctl") is not None and sys.platform.startswith("linux")


def service_state(env: Env) -> str:
    """``active`` / ``inactive`` / ``failed`` / ``absent`` (no unit) / ``unmanaged`` (no systemd)."""
    if not systemd_available(env):
        return "unmanaged"
    r = env.runner(["systemctl", "is-active", env.service])
    st = (r.stdout or "").strip()
    if st in ("active", "activating", "inactive", "failed", "deactivating"):
        # distinguish "no such unit" from "inactive"
        if st == "inactive":
            cat = env.runner(["systemctl", "cat", env.service])
            if cat.returncode != 0:
                return "absent"
        return st
    return "absent"


def ready(env: Env, timeout: float = 3.0) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{env.port}/ready"
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 — loopback only
            return True, resp.read().decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001 — any failure = not ready
        body = getattr(exc, "fp", None)
        detail = ""
        if body is not None:
            try:
                detail = body.read().decode("utf-8", "replace").strip()
            except Exception:  # noqa: BLE001
                detail = ""
        return False, detail or f"{type(exc).__name__}: {exc}"


def wait_ready(env: Env, *, tries: int = 60, delay: float = 2.0) -> bool:
    info(f"Waiting for /ready on port {env.port}…")
    for _ in range(tries):
        up, _ = ready(env)
        if up:
            ok("the server answers /ready")
            return True
        time.sleep(delay)
    warn(f"not ready after {int(tries * delay)} s — check: liberty-ops logs")
    return False


def migrations(env: Env) -> tuple[int, dict | None]:
    """``(exit_code, json)`` of ``liberty-admin migrate --dry-run`` (0 up to date, 2 pending, 1 error)."""
    r = env.runner([env.tool("liberty-admin"), *_admin_config_args(env), "migrate", "--dry-run"])
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    return r.returncode, data


def migrations_summary(code: int, data: dict | None) -> str:
    if data is None:
        return "? (could not query)"
    errs = [a for a in data.get("apps", []) if a.get("status") == "error"]
    if errs:
        return "error: " + "; ".join(f"{a['app']}: {a.get('reason', '')}" for a in errs)
    pend = [(a["app"], a.get("pending") or []) for a in data.get("apps", []) if a.get("pending")]
    if not pend:
        return "up to date" if data.get("apps") else "no migratable app plugin"
    return f"{data.get('pending_total', 0)} pending (" + ", ".join(f"{a}: {len(p)}" for a, p in pend) + ")"


def _admin_config_args(env: Env) -> list[str]:
    return ["--config-app", str(env.app_config)] if env.app_config.exists() else []


def last_upgrade(env: Env) -> str:
    try:
        from liberty.versioning import ConfigVersionStore

        rows = ConfigVersionStore(env.config_dir).list_upgrades()
    except Exception as exc:  # noqa: BLE001
        return f"? ({type(exc).__name__})"
    if not rows:
        return "none recorded"
    r = rows[0]
    return f"{r['component']} {r['from_version'] or '-'} → {r['to_version']} ({r['created_at'][:19]})"


def sqlite_file(env: Env) -> Path | None:
    """The default pool's SQLite file when the framework runs on SQLite (the pipx default)."""
    try:
        import tomllib

        doc = tomllib.loads(Path(env.settings.connectors.config_path).read_text(encoding="utf-8"))
        url = doc.get("pools", {}).get("default", {}).get("url", "")
    except (OSError, Exception):  # noqa: BLE001
        url = ""
    from liberty.config import substitute_env

    url = substitute_env(url) if url else ""
    if not url.startswith("sqlite"):
        return None
    path = url.split("///", 1)[1] if "///" in url else ""
    if not path or path == ":memory:":
        return None
    return Path(path)


def default_pool_url(env: Env) -> str | None:
    try:
        import tomllib

        from liberty.config import substitute_env

        doc = tomllib.loads(Path(env.settings.connectors.config_path).read_text(encoding="utf-8"))
        url = doc.get("pools", {}).get("default", {}).get("url", "")
        return substitute_env(url) if url else None
    except Exception:  # noqa: BLE001
        return None


# ── commands ──────────────────────────────────────────────────────────────────────────────
def cmd_status(env: Env, args: argparse.Namespace) -> int:
    fw, apps = dist_version(FRAMEWORK_DIST), dist_version(APPS_DIST, env.apps_dir or env.config_dir)
    up, body = ready(env)
    code, mig = migrations(env)
    print(f"{_C['bold']}Liberty Next — Python install{_C['nc']}")
    rows = [
        ("installer", installer(env)),
        ("framework", fw or "?"),
        ("apps bundle", apps or "not installed"),
        ("app.toml", str(env.app_config) + ("" if env.app_config.exists() else " (missing — defaults)")),
        ("config dir", str(env.config_dir)),
        ("apps dir", str(env.apps_dir) if env.apps_dir else "— (LIBERTY_APPS_DIR unset)"),
        ("service", f"{env.service}: {service_state(env)}"),
        ("/ready", body if up else f"NOT READY — {body}"),
        ("migrations", migrations_summary(code, mig)),
        ("last upgrade", last_upgrade(env)),
    ]
    for k, v in rows:
        print(f"  {k:<13} {v}")
    return 0


def _doctor(env: Env) -> tuple[int, list[str]]:
    """Runs every check, printing as it goes; returns ``(problems, printed_lines)``."""
    lines: list[str] = []
    problems = 0

    def p_ok(msg: str) -> None:
        ok(msg); lines.append(f"✔ {msg}")

    def p_warn(msg: str) -> None:
        warn(msg); lines.append(f"! {msg}")

    def p_fail(msg: str, fix: str) -> None:
        nonlocal problems
        problems += 1
        fail_line(msg, fix); lines.append(f"✗ {msg}"); lines.append(f"   → {fix}")

    # python + install
    if sys.version_info < (3, 12):
        p_fail(f"Python {sys.version.split()[0]} — the framework needs 3.12", "reinstall with python3.12 (pipx install --python python3.12 liberty-next)")
    else:
        p_ok(f"Python {sys.version.split()[0]}")
    fw = dist_version(FRAMEWORK_DIST)
    p_ok(f"framework {fw} via {installer(env)}") if fw else p_fail("liberty-next is not an installed distribution", "pipx install liberty-next")
    # secrets
    for key, minimum in (("LIBERTY_JWT_SECRET", 32), ("LIBERTY_MASTER_KEY", 16)):
        v = os.environ.get(key, "")
        if not v:
            p_fail(f"{key} is not set in this environment", "export it (openssl rand -base64 48) — under systemd: EnvironmentFile=/etc/liberty/secrets.env; run liberty-ops with the same file sourced")
        elif len(v) < minimum:
            p_fail(f"{key} is {len(v)} chars — below the {minimum} recommended", "rotate carefully: the master key encrypts every ENC: value; the JWT secret logs everyone out")
        else:
            p_ok(f"{key} set ({len(v)} chars)")
    # config files
    if env.app_config.exists():
        p_ok(f"app.toml: {env.app_config}")
    else:
        p_warn(f"no app.toml at {env.app_config} — running on defaults (set LIBERTY_APP_CONFIG to point at yours)")
    conn = Path(env.settings.connectors.config_path)
    if conn.exists():
        try:
            from liberty.connectors import load_connectors

            load_connectors(conn, master_key=env.settings.crypto.master_key)
            p_ok(f"connectors.toml parses: {conn}")
        except Exception as exc:  # noqa: BLE001
            p_fail(f"connectors.toml does not load: {type(exc).__name__}: {str(exc)[:160]}", "fix the file (Settings → Connectors cannot open while it is broken)")
    else:
        p_fail(f"connectors.toml missing: {conn}", "liberty-admin init-db seeds [pools.default]; liberty-apps install writes the apps' connectors")
    if env.apps_dir is not None:
        plugins = env.apps_dir.parent / "plugins"
        p_ok(f"LIBERTY_APPS_DIR={env.apps_dir}") if env.apps_dir.is_dir() else p_fail(f"LIBERTY_APPS_DIR={env.apps_dir} does not exist", f"liberty-apps install --target {env.apps_dir}")
        p_ok(f"plugins dir: {plugins}") if plugins.is_dir() else p_warn(f"no plugins dir at {plugins} (no app code / jobs)")
    else:
        p_warn("LIBERTY_APPS_DIR unset — open-framework install (no licensed apps)")
    # master key vs ENC: pool passwords
    try:
        import tomllib

        from liberty.crypto import decrypt_or_keep, is_encrypted

        doc = tomllib.loads(conn.read_text(encoding="utf-8")) if conn.exists() else {}
        bad = [n for n, c in doc.get("pools", {}).items()
               if is_encrypted(c.get("password", "")) and decrypt_or_keep(c["password"], env.settings.crypto.master_key or "")[1]]
        p_fail("the master key cannot decrypt the password of pool(s): " + ", ".join(bad), "LIBERTY_MASTER_KEY must be the key those ENC: values were made with") if bad else p_ok("master key decrypts every pool password")
    except Exception as exc:  # noqa: BLE001
        p_warn(f"could not check the pool passwords ({type(exc).__name__})")
    # the service + the server
    st = service_state(env)
    if st == "unmanaged":
        p_warn("no systemd here — start/stop/restart/logs print what to run by hand")
    elif st == "absent":
        p_warn(f"no systemd unit '{env.service}' — see the docs (Python server → Run under systemd), or set LIBERTY_SERVICE")
    elif st == "active":
        p_ok(f"service {env.service} active")
    else:
        p_fail(f"service {env.service} is {st}", "liberty-ops start   (then liberty-ops logs)")
    up, body = ready(env)
    if up:
        p_ok(f"/ready on port {env.port}: {body}")
    elif st in ("active", "unmanaged"):
        p_fail(f"/ready on port {env.port} fails: {body}", "the server is down or its framework database is unreachable — liberty-ops logs; check [pools.default]")
    else:
        p_warn(f"/ready on port {env.port} not answering (service not running)")
    code, mig = migrations(env)
    summ = migrations_summary(code, mig)
    if code == 2:
        p_fail(f"schema migrations: {summ}", "liberty-ops migrate")
    elif code == 1 or summ.startswith("error"):
        p_fail(f"schema migrations: {summ}", "liberty-ops migrate --dry-run for the details")
    elif summ.startswith("?"):
        p_warn(f"schema migrations: {summ}")
    else:
        p_ok(f"schema migrations: {summ}")
    # disk
    free_mb = shutil.disk_usage(env.config_dir if env.config_dir.exists() else Path.cwd()).free // (1024 * 1024)
    p_ok(f"disk free: {free_mb} MB") if free_mb > 1024 else p_fail(f"disk free: {free_mb} MB", "free space before upgrading (a wheel + its venv is ~500 MB)")
    print()
    if problems:
        print(f"{_C['red']}✗{_C['nc']} {problems} problem(s) — see the → lines above")
    else:
        ok("no problems found")
    return problems, lines


def cmd_doctor(env: Env, args: argparse.Namespace) -> int:
    problems, _ = _doctor(env)
    return 1 if problems else 0


def _systemctl(env: Env, verb: str) -> int:
    st = service_state(env)
    if st == "unmanaged":
        warn(f"no systemd — {verb} the server yourself (foreground: {env.tool('liberty-next')})")
        return 0
    if st == "absent":
        warn(f"no systemd unit '{env.service}' — nothing to {verb} (set LIBERTY_SERVICE if it has another name)")
        return 0
    cmd = ["systemctl", verb, env.service]
    if os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", *cmd]
    r = env.runner(cmd, capture=False)
    if r.returncode != 0:
        raise OpsError(f"systemctl {verb} {env.service} failed ({r.returncode})")
    ok(f"{env.service}: {verb} done")
    return 0


def cmd_start(env: Env, args: argparse.Namespace) -> int:
    rc = _systemctl(env, "start")
    if service_state(env) in ("active", "activating"):
        wait_ready(env)
    return rc


def cmd_stop(env: Env, args: argparse.Namespace) -> int:
    return _systemctl(env, "stop")


def cmd_restart(env: Env, args: argparse.Namespace) -> int:
    rc = _systemctl(env, "restart")
    if service_state(env) in ("active", "activating"):
        wait_ready(env)
    return rc


def cmd_logs(env: Env, args: argparse.Namespace) -> int:
    if not systemd_available(env):
        warn("no systemd — the server logs to the terminal it runs in")
        return 0
    cmd = ["journalctl", "-u", env.service, "-n", "200", "--no-pager"]
    if args.follow:
        cmd = ["journalctl", "-u", env.service, "-n", "200", "-f"]
    r = env.runner(cmd, capture=False)
    return r.returncode


def cmd_migrate(env: Env, args: argparse.Namespace) -> int:
    cmd = [env.tool("liberty-admin"), *_admin_config_args(env), "migrate"]
    if args.dry_run:
        cmd.append("--dry-run")
    r = env.runner(cmd, capture=False)
    return r.returncode


# ── backup / restore ──────────────────────────────────────────────────────────────────────
def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")


def do_backup(env: Env, dest: Path, keep_days: int | None) -> Path:
    """One timestamped directory: config.tar.gz (the config dir incl. .versions/), apps.tar.gz
    (LIBERTY_APPS_DIR's parent: config + plugins + data), liberty.sqlite (when the default pool
    is SQLite), default-pool.dump (pg_dump custom format, when it is Postgres and pg_dump is on
    the PATH), plus manifest.json. The Postgres dump is best-effort: a managed database usually
    has its own backups."""
    dest = dest.resolve()
    target = dest / _stamp()
    target.mkdir(parents=True, exist_ok=False)
    manifest: dict = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": socket.gethostname(),
                      "framework": dist_version(FRAMEWORK_DIST), "apps": dist_version(APPS_DIST), "items": {}}
    info(f"Destination: {target}")

    def tar_dir(src: Path, name: str) -> None:
        out = target / f"{name}.tar.gz"
        src = src.resolve()
        skip: list[str] = []
        try:  # the destination inside the tree being archived → leave it out
            skip.append("./" + dest.resolve().relative_to(src).as_posix())
        except ValueError:
            pass

        def _filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
            return None if any(ti.name == p or ti.name.startswith(p + "/") for p in skip) else ti

        with tarfile.open(out, "w:gz") as tf:
            tf.add(src, arcname=".", filter=_filter)
        manifest["items"][name] = {"from": str(src), "file": out.name, "bytes": out.stat().st_size}
        print(f"  {name:<14} ← {src}  ({out.stat().st_size // 1024} KB)")

    if env.config_dir.is_dir():
        tar_dir(env.config_dir, "config")
    if env.apps_dir is not None and env.apps_dir.parent.is_dir() and env.apps_dir.parent != env.config_dir:
        tar_dir(env.apps_dir.parent, "apps")
    db = sqlite_file(env)
    if db is not None and db.is_file():
        import sqlite3

        out = target / "liberty.sqlite"
        with sqlite3.connect(db) as src, sqlite3.connect(out) as dst:
            src.backup(dst)  # consistent copy even while the server runs
        manifest["items"]["sqlite"] = {"from": str(db), "file": out.name, "bytes": out.stat().st_size}
        print(f"  {'sqlite':<14} ← {db}  ({out.stat().st_size // 1024} KB)")
    url = default_pool_url(env) or ""
    if url.startswith("postgresql") and shutil.which("pg_dump"):
        sync_url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
        out = target / "default-pool.dump"
        r = env.runner(["pg_dump", "--format=custom", "--file", str(out), sync_url])
        if r.returncode == 0:
            manifest["items"]["postgres"] = {"file": out.name, "bytes": out.stat().st_size}
            print(f"  {'postgres':<14} ← default pool (pg_dump custom)  ({out.stat().st_size // 1024} KB)")
        else:
            warn(f"pg_dump failed ({r.returncode}) — {(r.stderr or '').strip()[:200]}; the database is NOT in this backup")
    elif url.startswith("postgresql"):
        warn("default pool is Postgres but pg_dump is not on the PATH — the database is NOT in this backup")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if keep_days:
        cutoff = time.time() - keep_days * 86400
        for d in dest.iterdir():
            if d.is_dir() and d != target and d.stat().st_mtime < cutoff and (d / "manifest.json").exists():
                shutil.rmtree(d)
                print(f"  swept {d.name} (older than {keep_days} days)")
    ok(f"backup complete: {target}")
    return target


def cmd_backup(env: Env, args: argparse.Namespace) -> int:
    dest = Path(args.dest) if args.dest else env.config_dir.parent / "backups"
    do_backup(env, dest, args.keep)
    return 0


def do_restore(env: Env, src: Path) -> list[str]:
    """Put a backup back: config dir, apps dir, SQLite file. The Postgres dump is NOT restored
    automatically (``pg_restore --clean --if-exists -d <url> default-pool.dump``) — dropping a
    live database is the operator's call. Returns the restored item names."""
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8")) if (src / "manifest.json").exists() else {"items": {}}
    done: list[str] = []

    def untar(name: str, into: Path) -> None:
        f = src / f"{name}.tar.gz"
        if not f.is_file():
            return
        into.mkdir(parents=True, exist_ok=True)
        with tarfile.open(f, "r:gz") as tf:
            members = [m for m in tf.getmembers() if not (m.name.startswith("/") or ".." in Path(m.name).parts)]
            tf.extractall(into, members=members, filter="data")
        info(f"restored {name} → {into}")
        done.append(name)

    untar("config", env.config_dir)
    if env.apps_dir is not None:
        untar("apps", env.apps_dir.parent)
    db = sqlite_file(env)
    if db is not None and (src / "liberty.sqlite").is_file():
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "liberty.sqlite", db)
        info(f"restored sqlite → {db}")
        done.append("sqlite")
    if "postgres" in manifest.get("items", {}):
        warn(f"Postgres dump present but not restored automatically: pg_restore --clean --if-exists -d <default pool url> {src / 'default-pool.dump'}")
    return done


def cmd_restore(env: Env, args: argparse.Namespace) -> int:
    src = Path(args.backup_dir)
    if not src.is_dir():
        raise OpsError(f"{src} is not a backup directory (made by liberty-ops backup)")
    if not args.yes:
        ans = input(f"Restore {src} over the live config / apps / SQLite data? Everything written since is lost. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            raise OpsError("restore cancelled")
    was_active = service_state(env) == "active"
    if was_active:
        _systemctl(env, "stop")
    done = do_restore(env, src)
    if was_active:
        _systemctl(env, "start")
        wait_ready(env)
    ok(f"restore done ({', '.join(done) or 'nothing matched'})")
    return 0


# ── upgrade / rollback ────────────────────────────────────────────────────────────────────
@dataclass
class Steps:
    items: list[str] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str | None = None) -> None:
        self.items.append(f"{name}={status}" + (f":{detail}" if detail else ""))

    def failed(self) -> list[str]:
        return [s.split("=")[0] for s in self.items if "=fail" in s]


def install_framework(env: Env, version: str | None) -> subprocess.CompletedProcess:
    how = installer(env)
    if how == "pipx":
        cmd = ["pipx", "install", "--force", f"{FRAMEWORK_DIST}[all]=={version}"] if version else ["pipx", "upgrade", FRAMEWORK_DIST]
    else:
        spec = f"{FRAMEWORK_DIST}[all]=={version}" if version else f"{FRAMEWORK_DIST}[all]"
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    info("$ " + " ".join(cmd))
    return env.runner(cmd, capture=False)


def install_apps_wheel(env: Env, wheel: Path) -> subprocess.CompletedProcess:
    how = installer(env)
    cmd = ["pipx", "inject", "--force", FRAMEWORK_DIST, str(wheel)] if how == "pipx" else [sys.executable, "-m", "pip", "install", "--upgrade", str(wheel)]
    info("$ " + " ".join(cmd))
    r = env.runner(cmd, capture=False)
    if r.returncode == 0 and env.apps_dir is not None:
        info("Merge plan")  # the per-entity config merge: added / refreshed / kept / removed
        env.runner([env.tool("liberty-apps"), "install", "--target", str(env.apps_dir), "--dry-run"], capture=False)
        r2 = env.runner([env.tool("liberty-apps"), "install", "--target", str(env.apps_dir)], capture=False)
        if r2.returncode != 0:
            return r2
    return r


def record_report(env: Env, *, kind: str, started: str, steps: Steps, before: dict, after: dict, backup: Path | None,
                  doctor_problems: int | None, doctor_text: str, migrations_json: str | None, health: str) -> str | None:
    """``liberty-admin record-upgrade`` through the (new) binary; returns the report name or None."""
    cmd = [env.tool("liberty-admin"), *_admin_config_args(env), "record-upgrade", "--kind", kind, "--started-at", started,
           "--host", socket.gethostname(), "--layout", f"python/{installer(env)}", "--chain", str(env.config_dir),
           "--tag-from", before.get("framework") or "", "--tag-to", after.get("framework") or "",
           "--fw-from", before.get("framework") or "", "--fw-to", after.get("framework") or "",
           "--apps-from", before.get("apps") or "", "--apps-to", after.get("apps") or "",
           "--health", health, "--doctor-text", "-", "--who", "liberty-ops"]
    if backup is not None:
        cmd += ["--backup", str(backup)]
    if doctor_problems is not None:
        cmd += ["--doctor-problems", str(doctor_problems)]
    if migrations_json:
        cmd += ["--migrations", migrations_json]
    for s in steps.items:
        cmd += ["--step", s]
    r = env.runner(cmd, stdin=doctor_text)
    if r.returncode != 0:
        warn(f"could not record the report ({(r.stderr or '').strip()[:200]}) — the run still completed")
        return None
    try:
        name = json.loads(r.stdout)["report"]
    except (json.JSONDecodeError, KeyError):
        return None
    ok(f"report recorded (Settings → History → Upgrades): {name}")
    return name


def _versions_after(env: Env) -> dict:
    """Versions as the NEW binaries report them (this process still holds the old modules)."""
    out: dict = {}
    py = env.bin_dir / "python"
    r = env.runner([str(py) if py.exists() else sys.executable, "-c", "import liberty;print(liberty.__version__)"])
    out["framework"] = (r.stdout or "").strip() or None
    r = env.runner([env.tool("liberty-apps"), "--version"])
    txt = (r.stdout or "").strip()
    out["apps"] = txt.split()[-1] if r.returncode == 0 and txt else None
    return out


def cmd_upgrade(env: Env, args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    steps = Steps()
    before = {"framework": dist_version(FRAMEWORK_DIST), "apps": dist_version(APPS_DIST)}
    wheel = Path(args.apps).resolve() if args.apps else None
    if wheel is not None and not wheel.is_file():
        raise OpsError(f"apps wheel not found: {wheel}")

    info("1/6 Pre-flight (advisory — an upgrade is often the fix for what doctor reports; the backup is the gate)")
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        problems, lines = _doctor(env)
    print(buf.getvalue(), end="")
    doctor_text = "\n".join(lines) + "\n"
    steps.add("doctor", "ok" if problems == 0 else "warn", None if problems == 0 else f"{problems} problem(s) before the upgrade")

    info(f"2/6 Backup (gate: an upgrade never starts without one; --keep {args.keep} days)")
    dest = Path(args.backup_dest) if args.backup_dest else env.config_dir.parent / "backups"
    try:
        bdir = do_backup(env, dest, args.keep)
    except Exception as exc:  # noqa: BLE001
        raise OpsError(f"backup failed ({type(exc).__name__}: {exc}) — upgrade aborted, nothing was changed") from exc
    steps.add("backup", "ok", str(bdir))
    env.save_state(previous_framework=before["framework"], previous_apps=before["apps"], last_backup=str(bdir),
                   previous_apps_wheel=env.load_state().get("current_apps_wheel"))

    info("3/6 Framework" + (f" → {args.version}" if args.version else " → latest"))
    r = install_framework(env, args.version)
    steps.add("framework", "ok" if r.returncode == 0 else "fail", None if r.returncode == 0 else f"installer exited {r.returncode}")
    if r.returncode != 0:
        warn("the framework install failed — the previous version is still what runs; nothing else is changed")

    if wheel is not None:
        info(f"4/6 Apps wheel {wheel.name}")
        r = install_apps_wheel(env, wheel)
        steps.add("apps", "ok" if r.returncode == 0 else "fail", wheel.name if r.returncode == 0 else f"exited {r.returncode}")
        if r.returncode == 0:
            env.save_state(current_apps_wheel=str(wheel))
    else:
        info("4/6 Apps wheel — none given, apps bundle unchanged")

    info("5/6 Schema migrations")
    r = env.runner([env.tool("liberty-admin"), *_admin_config_args(env), "migrate"])
    mig_json = r.stdout.strip() if r.stdout and r.stdout.strip() else None
    if mig_json:
        print(mig_json)
    steps.add("migrate", "ok" if r.returncode == 0 else "fail", None if r.returncode == 0 else f"liberty-admin migrate exited {r.returncode}")

    health = "not restarted"
    if args.no_restart:
        warn("--no-restart: the running server still runs the previous code until you restart it")
        steps.add("restart", "skipped", "--no-restart")
    else:
        st = service_state(env)
        if st in ("unmanaged", "absent"):
            warn(f"service {env.service}: {st} — restart the server yourself for the new code to run")
            steps.add("restart", "skipped", f"service {st}")
        else:
            info(f"6/6 Restart {env.service}")
            try:
                _systemctl(env, "restart")
                healthy = wait_ready(env)
                health = "ready" if healthy else "not ready"
                steps.add("restart", "ok" if healthy else "fail", None if healthy else "/ready did not answer")
            except OpsError as exc:
                steps.add("restart", "fail", str(exc))
                health = "restart failed"
    after = _versions_after(env)
    record_report(env, kind="upgrade", started=started, steps=steps, before=before, after=after, backup=bdir,
                  doctor_problems=problems, doctor_text=doctor_text, migrations_json=mig_json, health=health)
    print()
    cmd_status(env, args)
    if steps.failed():
        print()
        warn(f"some steps FAILED ({', '.join(steps.failed())}) — 'liberty-ops rollback' goes back to {before['framework']} and the backup above")
        return 1
    return 0


def cmd_rollback(env: Env, args: argparse.Namespace) -> int:
    st = env.load_state()
    prev, backup = st.get("previous_framework"), st.get("last_backup")
    if not prev:
        raise OpsError("nothing to roll back to — 'liberty-ops upgrade' records the previous version and a backup first")
    bpath = Path(backup) if backup else None
    if bpath is None or not bpath.is_dir():
        raise OpsError(f"the backup taken before the last upgrade ({backup}) is gone — restore one by hand: liberty-ops restore <dir>")
    wheel = Path(args.apps).resolve() if args.apps else (Path(st["previous_apps_wheel"]) if st.get("previous_apps_wheel") and Path(st["previous_apps_wheel"]).is_file() else None)
    cur = {"framework": dist_version(FRAMEWORK_DIST), "apps": dist_version(APPS_DIST)}
    print(f"{_C['bold']}Rollback{_C['nc']}\n  framework  {cur['framework']} → {prev}\n  apps       "
          + (f"{cur['apps']} → wheel {wheel.name}" if wheel else f"{cur['apps'] or '-'} (unchanged — pass --apps <previous wheel> to downgrade the bundle)")
          + f"\n  data       restored from {bpath}")
    if not args.yes:
        ans = input("Everything written since that backup will be lost. Continue? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            raise OpsError("rollback cancelled")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    steps = Steps()
    # keep the upgrade's report across the config restore (it lives in .versions/)
    saved_report: str | None = None
    try:
        from liberty.versioning import ConfigVersionStore

        rows = [r for r in ConfigVersionStore(env.config_dir).list_upgrades() if r.get("report") and r["kind"] == "upgrade"]
        if rows:
            data, _ = ConfigVersionStore(env.config_dir).read_upgrade_report(rows[0]["report"])
            saved_report = json.dumps(data) if data else None
    except Exception:  # noqa: BLE001
        saved_report = None
    was_active = service_state(env) == "active"
    if was_active:
        _systemctl(env, "stop")
    r = install_framework(env, prev)
    steps.add("framework", "ok" if r.returncode == 0 else "fail", prev if r.returncode == 0 else f"installer exited {r.returncode}")
    if wheel is not None:
        r = install_apps_wheel(env, wheel)
        steps.add("apps", "ok" if r.returncode == 0 else "fail", wheel.name)
    try:
        done = do_restore(env, bpath)
        steps.add("restore", "ok", ", ".join(done))
    except Exception as exc:  # noqa: BLE001
        steps.add("restore", "fail", f"{type(exc).__name__}: {exc}")
    if saved_report:
        r = env.runner([env.tool("liberty-admin"), *_admin_config_args(env), "record-upgrade", "--from-json", "-"], stdin=saved_report)
        steps.add("keep-upgrade-report", "ok" if r.returncode == 0 else "warn")
    health = "not restarted"
    if was_active:
        try:
            _systemctl(env, "start")
            health = "ready" if wait_ready(env) else "not ready"
        except OpsError as exc:
            health = str(exc)
    after = _versions_after(env)
    record_report(env, kind="rollback", started=started, steps=steps, before=cur, after=after, backup=bpath,
                  doctor_problems=None, doctor_text="", migrations_json=None, health=health)
    env.save_state(previous_framework=None, last_backup=None)
    ok(f"rolled back to {after.get('framework') or prev}")
    cmd_status(env, args)
    return 1 if steps.failed() else 0


# ── parser ────────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="liberty-ops", description=__doc__.split("\n\n")[0])
    p.add_argument("--config-app", help="app.toml (default: LIBERTY_APP_CONFIG, else config/app.toml)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="versions, config, service, /ready, migrations, last upgrade").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="what is wrong and how to fix it (exit 1 when something is)").set_defaults(func=cmd_doctor)
    sub.add_parser("start", help="systemctl start + wait for /ready").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="systemctl stop").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="systemctl restart + wait for /ready").set_defaults(func=cmd_restart)
    pl = sub.add_parser("logs", help="journalctl for the service"); pl.add_argument("-f", "--follow", action="store_true"); pl.set_defaults(func=cmd_logs)
    pm = sub.add_parser("migrate", help="apply the apps' pending schema migrations (liberty-admin migrate)"); pm.add_argument("--dry-run", action="store_true"); pm.set_defaults(func=cmd_migrate)
    pb = sub.add_parser("backup", help="config dir, apps dir, SQLite file, pg_dump → <dest>/<stamp>/")
    pb.add_argument("--dest", help="where (default: <config dir>/../backups)"); pb.add_argument("--keep", type=int, help="delete backups older than N days"); pb.set_defaults(func=cmd_backup)
    pr = sub.add_parser("restore", help="put a backup back (stops / starts the service around it)")
    pr.add_argument("backup_dir"); pr.add_argument("--yes", action="store_true"); pr.set_defaults(func=cmd_restore)
    pu = sub.add_parser("upgrade", help="doctor → backup → install → apps → migrate → restart → report → status")
    pu.add_argument("--version", help="pin this framework version (default: latest)")
    pu.add_argument("--apps", help="a liberty-apps wheel to install too")
    pu.add_argument("--keep", type=int, default=30, help="backup retention in days (default 30)")
    pu.add_argument("--backup-dest", help="where backups go (default: <config dir>/../backups)")
    pu.add_argument("--no-restart", action="store_true", help="do not restart the service")
    pu.set_defaults(func=cmd_upgrade)
    po = sub.add_parser("rollback", help="previous framework version + the pre-upgrade backup")
    po.add_argument("--yes", action="store_true"); po.add_argument("--apps", help="the previous apps wheel, to downgrade the bundle too"); po.set_defaults(func=cmd_rollback)
    return p


def main(argv: list[str] | None = None, *, runner: Runner = run) -> int:
    args = build_parser().parse_args(argv)
    try:
        env = resolve_env(args, runner=runner)
        return int(args.func(env, args) or 0)
    except OpsError as exc:
        print(f"{_C['red']}✗{_C['nc']} {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
