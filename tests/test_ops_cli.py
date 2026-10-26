"""``liberty-ops`` — the Python-install twin of ``release/liberty``. Every subprocess goes through
one seam (``runner``), replaced here by a scripted fake; the HTTP probe is monkeypatched."""

from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from liberty import ops_cli
from liberty.ops_cli import Env, Steps, do_backup, do_restore, installer, migrations_summary, service_state


class FakeRunner:
    """Records every command; answers from `responses` ({prefix-tuple: (rc, stdout)}), default rc 0."""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self.responses = responses or {}

    def __call__(self, cmd, *, check=False, capture=True, env=None, stdin=None):
        self.calls.append([str(c) for c in cmd])
        self.stdins.append(stdin)
        key_cmd = [Path(c).name if "/" in str(c) else str(c) for c in cmd]
        if key_cmd and key_cmd[0].startswith("python"):  # sys.executable may be python3.12
            key_cmd[0] = "python"
        for prefix, (rc, out) in self.responses.items():
            if tuple(key_cmd[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(cmd, rc, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def names(self) -> list[str]:
        return [" ".join(Path(c[0]).name.split()[:1] + c[1:3]) for c in self.calls]


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Env:
    cfg = tmp_path / "apps" / "config"
    cfg.mkdir(parents=True)
    (tmp_path / "apps" / "plugins").mkdir()
    (cfg / "connectors.toml").write_text(f'[pools.default]\nurl = "sqlite+aiosqlite:///{tmp_path / "data" / "fw.db"}"\n')
    (tmp_path / "data").mkdir()
    import sqlite3

    with sqlite3.connect(tmp_path / "data" / "fw.db") as c:
        c.execute("CREATE TABLE t (x INTEGER)"); c.execute("INSERT INTO t VALUES (1)")
    app_toml = tmp_path / "app.toml"
    app_toml.write_text(f'[auth]\nbackend = "toml"\ntoml_path = "{cfg / "auth.toml"}"\n[connectors]\nconfig_path = "{cfg / "connectors.toml"}"\n')
    monkeypatch.setenv("LIBERTY_APPS_DIR", str(cfg))
    monkeypatch.setenv("LIBERTY_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("LIBERTY_MASTER_KEY", "y" * 32)
    monkeypatch.setattr(ops_cli, "systemd_available", lambda env: True)
    monkeypatch.setattr(ops_cli.shutil, "which", lambda name: "/usr/bin/" + name if name in ("pipx", "systemctl") else None)
    monkeypatch.setattr(ops_cli, "ready", lambda env, timeout=3.0: (True, '{"status":"ok"}'))
    monkeypatch.setattr(ops_cli.time, "sleep", lambda s: None)
    args = argparse.Namespace(config_app=str(app_toml))
    e = ops_cli.resolve_env(args, runner=FakeRunner())
    e.bin_dir = tmp_path / "bin"  # tools fall back to bare names
    return e


def _ns(**kw) -> argparse.Namespace:
    base = {"dest": None, "keep": None, "yes": True, "version": None, "apps": None, "backup_dest": None,
            "no_restart": False, "dry_run": False, "follow": False}
    base.update(kw)
    return argparse.Namespace(**base)


# ── probes ──────────────────────────────────────────────────────────────────────────────────
def test_installer_detection(env) -> None:
    env.runner = FakeRunner({("pipx", "list"): (0, json.dumps({"venvs": {"liberty-next": {}}}))})
    assert installer(env) == "pipx"
    env.runner = FakeRunner({("pipx", "list"): (0, json.dumps({"venvs": {}}))})
    assert installer(env) == "pip"


def test_service_state(env) -> None:
    env.runner = FakeRunner({("systemctl", "is-active"): (0, "active\n")})
    assert service_state(env) == "active"
    env.runner = FakeRunner({("systemctl", "is-active"): (3, "inactive\n"), ("systemctl", "cat"): (1, "")})
    assert service_state(env) == "absent"
    env.runner = FakeRunner({("systemctl", "is-active"): (3, "inactive\n"), ("systemctl", "cat"): (0, "[Unit]")})
    assert service_state(env) == "inactive"


def test_migrations_summary() -> None:
    assert migrations_summary(1, None) == "? (could not query)"
    assert migrations_summary(0, {"apps": []}) == "no migratable app plugin"
    assert migrations_summary(0, {"apps": [{"app": "a", "pending": []}]}) == "up to date"
    assert migrations_summary(2, {"apps": [{"app": "a", "pending": ["r1", "r2"]}], "pending_total": 2}) == "2 pending (a: 2)"
    assert migrations_summary(1, {"apps": [{"app": "a", "status": "error", "reason": "boom"}]}) == "error: a: boom"


# ── backup / restore ────────────────────────────────────────────────────────────────────────
def test_backup_and_restore_round_trip(env, tmp_path: Path) -> None:
    dest = env.apps_dir.parent / "backups"  # INSIDE the apps tree on purpose — must not tar itself
    target = do_backup(env, dest, keep_days=None)
    names = sorted(p.name for p in target.iterdir())
    assert names == ["apps.tar.gz", "config.tar.gz", "liberty.sqlite", "manifest.json"]
    with tarfile.open(target / "apps.tar.gz") as tf:
        members = tf.getnames()
    assert "./config/connectors.toml" in members and "./plugins" in members
    assert not any(m.startswith("./backups") for m in members)
    manifest = json.loads((target / "manifest.json").read_text())
    assert set(manifest["items"]) == {"config", "apps", "sqlite"}
    # mutate, then restore
    (env.config_dir / "connectors.toml").write_text("# clobbered\n")
    import sqlite3

    with sqlite3.connect(tmp_path / "data" / "fw.db") as c:
        c.execute("DELETE FROM t")
    done = do_restore(env, target)
    assert done == ["config", "apps", "sqlite"]
    assert "[pools.default]" in (env.config_dir / "connectors.toml").read_text()
    with sqlite3.connect(tmp_path / "data" / "fw.db") as c:
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 1


def test_backup_retention(env, tmp_path: Path) -> None:
    import os
    import time

    dest = tmp_path / "bk"
    old = dest / "2020-01-01_000000"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text("{}")
    stale = time.time() - 90 * 86400
    os.utime(old, (stale, stale))
    unrelated = dest / "keep-me"
    unrelated.mkdir()
    os.utime(unrelated, (stale, stale))
    do_backup(env, dest, keep_days=30)
    assert not old.exists() and unrelated.exists()  # only our own stamped dirs are swept


# ── upgrade / rollback ──────────────────────────────────────────────────────────────────────
def _upgrade_runner(fw_after: str = "7.0.58") -> FakeRunner:
    return FakeRunner({
        ("pipx", "list"): (0, json.dumps({"venvs": {"liberty-next": {}}})),
        ("systemctl", "is-active"): (0, "active\n"),
        ("liberty-admin", "--config-app"): (0, ""),  # placeholder, refined per call below
        ("python", "-c"): (0, fw_after + "\n"),
        ("liberty-apps", "--version"): (0, "liberty-apps 7.0.58\n"),
    })


class AdminAwareRunner(FakeRunner):
    """liberty-admin calls answer per subcommand (migrate → JSON, record-upgrade → report JSON)."""

    def __call__(self, cmd, **kw):
        names = [Path(str(c)).name for c in cmd]
        if names and names[0] == "liberty-admin":
            self.calls.append([str(c) for c in cmd]); self.stdins.append(kw.get("stdin"))
            if "migrate" in names:
                rc = 2 if "--dry-run" in names and getattr(self, "pending", False) else 0
                return subprocess.CompletedProcess(cmd, rc, json.dumps({"apps": [], "pending_total": 0}), "")
            if "record-upgrade" in names:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"id": 1, "report": "r-1"}), "")
        return super().__call__(cmd, **kw)


def test_upgrade_runs_every_phase_and_records(env, monkeypatch) -> None:
    r = AdminAwareRunner(_upgrade_runner().responses)
    env.runner = r
    monkeypatch.setattr(ops_cli, "dist_version", lambda name: {"liberty-next": "7.0.57", "liberty-apps": None}[name])
    rc = ops_cli.cmd_upgrade(env, _ns(keep=30, backup_dest=str(env.config_dir.parent / "bk")))
    assert rc == 0
    flat = [" ".join(Path(c[0]).name.split() + c[1:]) for c in r.calls]
    assert any(c.startswith("pipx upgrade liberty-next") for c in flat), flat
    assert any("systemctl restart liberty-next" in c for c in flat)
    rec = next(c for c in r.calls if "record-upgrade" in c)
    assert "--kind" in rec and rec[rec.index("--kind") + 1] == "upgrade"
    assert rec[rec.index("--fw-from") + 1] == "7.0.57" and rec[rec.index("--fw-to") + 1] == "7.0.58"
    steps = [rec[i + 1] for i, a in enumerate(rec) if a == "--step"]
    assert [s.split("=")[0] for s in steps] == ["doctor", "backup", "framework", "migrate", "restart"]
    assert all("=ok" in s or s.startswith("doctor=") for s in steps)
    # --config-app precedes the subcommand on every liberty-admin call
    for c in r.calls:
        if Path(c[0]).name == "liberty-admin":
            assert c[1] == "--config-app"
    st = env.load_state()
    assert st["previous_framework"] == "7.0.57" and Path(st["last_backup"]).is_dir()


def test_upgrade_aborts_when_backup_fails(env, monkeypatch) -> None:
    env.runner = AdminAwareRunner(_upgrade_runner().responses)
    monkeypatch.setattr(ops_cli, "do_backup", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ops_cli.OpsError, match="backup failed"):
        ops_cli.cmd_upgrade(env, _ns())
    assert not any("pipx" in c[0] and "upgrade" in c for c in env.runner.calls)  # nothing installed


def test_upgrade_pins_version_and_installs_apps_wheel(env, monkeypatch, tmp_path: Path) -> None:
    r = AdminAwareRunner(_upgrade_runner().responses)
    env.runner = r
    monkeypatch.setattr(ops_cli, "dist_version", lambda name: "7.0.57" if name == "liberty-next" else None)
    wheel = tmp_path / "liberty_apps-7.0.58-py3-none-any.whl"
    wheel.write_bytes(b"PK")
    rc = ops_cli.cmd_upgrade(env, _ns(version="7.0.58", apps=str(wheel), no_restart=True))
    assert rc == 0
    flat = [" ".join(c) for c in r.calls]
    assert any("pipx install --force liberty-next[all]==7.0.58" in c for c in flat)
    assert any(f"pipx inject --force liberty-next {wheel}" in c for c in flat)
    assert any("liberty-apps install --target" in c for c in flat)
    assert not any("systemctl restart" in c for c in flat)
    assert env.load_state()["current_apps_wheel"] == str(wheel)


def test_upgrade_reports_a_failed_install(env, monkeypatch) -> None:
    resp = _upgrade_runner().responses | {("pipx", "upgrade"): (1, "")}
    r = AdminAwareRunner(resp)
    env.runner = r
    monkeypatch.setattr(ops_cli, "dist_version", lambda name: "7.0.57" if name == "liberty-next" else None)
    rc = ops_cli.cmd_upgrade(env, _ns(no_restart=True))
    assert rc == 1
    rec = next(c for c in r.calls if "record-upgrade" in c)
    assert any(s.startswith("framework=fail") for s in rec)


def test_rollback_uses_the_recorded_state(env, monkeypatch) -> None:
    from liberty.versioning import ConfigVersionStore

    r = AdminAwareRunner(_upgrade_runner("7.0.57").responses)
    env.runner = r
    backup = do_backup(env, env.config_dir.parent / "bk", None)
    env.save_state(previous_framework="7.0.57", last_backup=str(backup))
    store = ConfigVersionStore(env.config_dir)
    store.write_upgrade_report("r-up", {"kind": "upgrade", "framework": {"from": "7.0.57", "to": "7.0.58"}}, "# up")
    store.record_upgrade(component="stack", kind="upgrade", to_version="7.0.58", report="r-up")
    monkeypatch.setattr(ops_cli, "dist_version", lambda name: "7.0.58" if name == "liberty-next" else None)
    rc = ops_cli.cmd_rollback(env, _ns(yes=True))
    assert rc == 0
    flat = [" ".join(c) for c in r.calls]
    assert any("pipx install --force liberty-next[all]==7.0.57" in c for c in flat)
    assert any("systemctl stop" in c for c in flat) and any("systemctl start" in c for c in flat)
    rejournal = next(c for c in r.calls if "--from-json" in c)
    assert json.loads(r.stdins[r.calls.index(rejournal)])["kind"] == "upgrade"  # the upgrade's report is carried over
    rec = next(c for c in r.calls if "record-upgrade" in c and "--kind" in c)
    assert rec[rec.index("--kind") + 1] == "rollback"
    assert env.load_state()["previous_framework"] is None  # consumed


def test_rollback_without_state_or_backup(env) -> None:
    with pytest.raises(ops_cli.OpsError, match="nothing to roll back"):
        ops_cli.cmd_rollback(env, _ns(yes=True))
    env.save_state(previous_framework="7.0.57", last_backup=str(env.config_dir / "gone"))
    with pytest.raises(ops_cli.OpsError, match="is gone"):
        ops_cli.cmd_rollback(env, _ns(yes=True))


def test_doctor_exit_code_and_main(env, monkeypatch, capsys) -> None:
    env.runner = AdminAwareRunner({("systemctl", "is-active"): (0, "active\n"), ("pipx", "list"): (0, "{}")})
    assert ops_cli.cmd_doctor(env, _ns()) == 0
    monkeypatch.delenv("LIBERTY_MASTER_KEY")
    assert ops_cli.cmd_doctor(env, _ns()) == 1
    out = capsys.readouterr().out
    assert "LIBERTY_MASTER_KEY is not set" in out and "→" in out
    # main() wires the parser + env; an OpsError is a clean exit 1
    rc = ops_cli.main(["--config-app", str(env.app_config), "rollback", "--yes"], runner=env.runner)
    assert rc == 1


def test_steps_helper() -> None:
    s = Steps()
    s.add("a", "ok"); s.add("b", "fail", "why"); s.add("c", "warn", "x:y")
    assert s.items == ["a=ok", "b=fail:why", "c=warn:x:y"] and s.failed() == ["b"]
