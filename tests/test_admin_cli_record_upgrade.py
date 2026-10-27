"""``liberty-admin record-upgrade`` — the report `release/liberty upgrade|rollback` hands in,
stored under .versions/upgrades/ and journaled as a ``stack`` row of the upgrade history."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liberty.admin_cli import main, render_upgrade_report
from liberty.versioning import ConfigVersionStore


@pytest.fixture
def env(tmp_path: Path):
    app_toml = tmp_path / "app.toml"
    app_toml.write_text('[auth]\nbackend = "toml"\n' f'toml_path = "{tmp_path / "auth.toml"}"\n')
    conn_toml = tmp_path / "connectors.toml"
    conn_toml.write_text(f'[pools.default]\nurl = "sqlite+aiosqlite:///{tmp_path / "fw.db"}"\n')
    return ["--config-app", str(app_toml), "--config-connectors", str(conn_toml)], tmp_path


def _run(base, *args, stdin: str | None, capsys, monkeypatch):
    if stdin is not None:
        import io
        import sys

        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = main([*base, "record-upgrade", *args])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_report_is_written_and_journaled(env, capsys, monkeypatch) -> None:
    base, root = env
    mig = {"apps": [{"app": "nomasx1", "target_connector": "nomasx1", "status": "applied", "pending": ["0009_x"]}],
           "pending_total": 1}
    rc, out = _run(
        base, "--kind", "upgrade", "--layout", "light", "--chain", "docker-compose.light.yml",
        "--tag-from", "latest", "--tag-to", "7.0.58", "--fw-from", "7.0.57", "--fw-to", "7.0.58",
        "--backup", "/srv/backups/2026", "--health", "healthy", "--doctor-problems", "1",
        "--doctor-text", "-", "--migrations", json.dumps(mig),
        "--step", "doctor=warn:1 problem", "--step", "backup=ok", "--step", "migrate=ok",
        stdin="✔ ok\n✗ bad\n   → fix\n", capsys=capsys, monkeypatch=monkeypatch,
    )
    assert rc == 0 and out["failed_steps"] == []
    store = ConfigVersionStore(root)
    row = store.get_upgrade(out["id"])
    assert row["component"] == "stack" and row["kind"] == "upgrade"
    assert row["from_version"] == "7.0.57" and row["to_version"] == "7.0.58"
    assert row["report"] == out["report"] and row["summary"].startswith("upgrade: framework 7.0.57 → 7.0.58")
    data, md = store.read_upgrade_report(row["report"])
    assert data["backup"] == "/srv/backups/2026" and data["migrations"] == mig
    assert data["doctor"] == {"problems": 1, "text": "✔ ok\n✗ bad\n   → fix\n"}
    assert [s["status"] for s in data["steps"]] == ["warn", "ok", "ok"]
    assert "| Framework | 7.0.57 → 7.0.58 |" in md and "**nomasx1** on `nomasx1`: applied — 1 revision(s): 0009_x" in md
    assert "✗ bad" in md  # doctor output quoted verbatim
    # newest first in the list, report name rides along
    assert store.list_upgrades()[0]["report"] == row["report"]


def test_failed_step_lands_in_the_summary(env, capsys, monkeypatch) -> None:
    base, root = env
    rc, out = _run(base, "--kind", "rollback", "--fw-to", "7.0.57", "--step", "restore=fail:tar exited 2",
                   stdin=None, capsys=capsys, monkeypatch=monkeypatch)
    assert rc == 0 and out["failed_steps"] == ["restore"]
    row = ConfigVersionStore(root).get_upgrade(out["id"])
    assert row["kind"] == "rollback" and row["summary"].endswith("FAILED: restore")


def test_bad_inputs(env, capsys, monkeypatch) -> None:
    base, _ = env
    rc, _out = _run(base, "--kind", "upgrade", "--migrations", "{not json", stdin=None, capsys=capsys, monkeypatch=monkeypatch)
    assert rc == 2
    with pytest.raises(SystemExit):  # argparse rejects an unknown step status
        main([*base, "record-upgrade", "--kind", "upgrade", "--step", "x=maybe"])


def test_render_without_optional_sections() -> None:
    md = render_upgrade_report({"kind": "upgrade", "finished_at": "2026-10-26T10:00:00+00:00",
                                "framework": {"from": None, "to": "7.0.58"}, "apps": {}, "image_tag": {}})
    assert md.startswith("# Upgrade — 2026-10-26 10:00:00 UTC") and "| Apps | not installed |" in md
    assert "## Steps" not in md and "## Doctor" not in md


def test_report_name_is_a_plain_stem(tmp_path: Path) -> None:
    store = ConfigVersionStore(tmp_path)
    with pytest.raises(ValueError):
        store.write_upgrade_report("../escape", {}, "")
    assert store.read_upgrade_report("../escape") == (None, None)
    assert store.read_upgrade_report("missing") == (None, None)


def test_from_json_rejournals_a_saved_report(env, capsys, monkeypatch) -> None:
    """The rollback saves the upgrade's report before restoring the config volume (which holds
    .versions/) and re-journals it afterwards: same name, same timestamp, same content."""
    base, root = env
    rc, first = _run(base, "--kind", "upgrade", "--fw-from", "7.0.57", "--fw-to", "7.0.58",
                     "--step", "pull=fail:registry down", stdin=None, capsys=capsys, monkeypatch=monkeypatch)
    assert rc == 0
    store = ConfigVersionStore(root)
    saved = Path(first["json_path"]).read_text()
    original_row = store.get_upgrade(first["id"])
    # simulate the restore: the report files + the history row are gone
    Path(first["json_path"]).unlink(); Path(first["markdown_path"]).unlink()
    rc, again = _run(base, "--from-json", "-", stdin=saved, capsys=capsys, monkeypatch=monkeypatch)
    assert rc == 0 and again["report"] == first["report"] and again["failed_steps"] == ["pull"]
    row = store.get_upgrade(again["id"])
    assert row["created_at"] == original_row["created_at"] and row["summary"] == original_row["summary"]
    data, md = store.read_upgrade_report(again["report"])
    assert data == json.loads(saved) and "registry down" in md


def test_from_json_needs_a_report_object(env, capsys, monkeypatch) -> None:
    base, _ = env
    assert _run(base, "--from-json", "-", stdin="[1,2]", capsys=capsys, monkeypatch=monkeypatch)[0] == 2
    assert _run(base, stdin=None, capsys=capsys, monkeypatch=monkeypatch)[0] == 2  # neither --kind nor --from-json
