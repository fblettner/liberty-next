from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from liberty.versioning import ConfigVersionStore


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_snapshot_numbers_dedupes_and_lists(tmp_path: Path) -> None:
    cfg = tmp_path / "screens.toml"
    _write(cfg, "v1")
    store = ConfigVersionStore(tmp_path)

    a = store.snapshot(cfg, source="manual", who="alice", comment="first")
    assert a is not None and a.version_num == 1 and a.who == "alice"
    # Unchanged content → content-addressed no-op (no new version).
    assert store.snapshot(cfg) is None
    _write(cfg, "v2")
    b = store.snapshot(cfg, who="bob")
    assert b is not None and b.version_num == 2

    versions = store.list_versions(cfg)
    assert [v.version_num for v in versions] == [2, 1]   # newest first
    assert store.content(a.id) == b"v1" and store.content(b.id) == b"v2"


def test_restore_writes_back_and_keeps_history(tmp_path: Path) -> None:
    cfg = tmp_path / "dictionary.toml"
    _write(cfg, "original")
    store = ConfigVersionStore(tmp_path)
    # Real save-hook flow: snapshot the PRIOR content, then the save overwrites the file.
    v1 = store.snapshot(cfg)                    # v1 = 'original' (captured before the edit)
    assert v1 is not None
    _write(cfg, "edited")                       # the save wrote new content; live='edited' (not yet a version)

    live = store.restore(v1.id, who="carol")    # roll back to v1 ('original')
    assert live == cfg and cfg.read_text() == "original"
    # Restore snapshotted the live 'edited' first (it wasn't a version yet) → nothing lost.
    versions = store.list_versions(cfg)
    assert [v.version_num for v in versions] == [2, 1]
    assert store.content(2) == b"edited" and any(v.source == "restore" for v in versions)


def test_snapshot_missing_file_is_noop(tmp_path: Path) -> None:
    store = ConfigVersionStore(tmp_path)
    assert store.snapshot(tmp_path / "nope.toml") is None
    assert store.list_versions(tmp_path / "nope.toml") == []


def test_store_persists_across_instances(tmp_path: Path) -> None:
    cfg = tmp_path / "menus.toml"
    _write(cfg, "a")
    ConfigVersionStore(tmp_path).snapshot(cfg)
    # A fresh store over the same dir reads the existing SQLite index.
    again = ConfigVersionStore(tmp_path)
    assert [v.version_num for v in again.list_versions(cfg)] == [1]


def test_snapshot_bytes_versions_synthetic_key(tmp_path: Path) -> None:
    # Screen+dependency bundles store arbitrary bytes under a synthetic ``screen:<app>:<id>`` key.
    store = ConfigVersionStore(tmp_path)
    key = "screen:nomasx1:f0093"
    a = store.snapshot_bytes(key, b"PK-bundle-1", source="save", comment="f0093 + 3 deps")
    assert a is not None and a.version_num == 1 and a.rel_path == key
    # Content-addressed: identical bytes → no new version.
    assert store.snapshot_bytes(key, b"PK-bundle-1") is None
    b = store.snapshot_bytes(key, b"PK-bundle-2", source="save")
    assert b is not None and b.version_num == 2
    # Synthetic filename flattens the ':' separators (no nested dirs from the key).
    assert ":" not in Path(b.snapshot_path).name
    assert store.content(a.id) == b"PK-bundle-1" and store.content(b.id) == b"PK-bundle-2"
    assert [v.version_num for v in store.list_versions(key)] == [2, 1]


def test_delete_removes_snapshot_and_row(tmp_path: Path) -> None:
    cfg = tmp_path / "screens.toml"
    store = ConfigVersionStore(tmp_path)
    _write(cfg, "a"); v1 = store.snapshot(cfg)
    _write(cfg, "b"); v2 = store.snapshot(cfg)
    assert v1 is not None and v2 is not None
    snap1 = Path(v1.snapshot_path)
    assert snap1.exists()
    assert store.delete(v1.id) is True
    assert not snap1.exists()                       # file gone too
    assert [v.version_num for v in store.list_versions(cfg)] == [2]
    assert store.delete(999999) is False            # unknown id


def test_delete_key_clears_whole_history(tmp_path: Path) -> None:
    cfg = tmp_path / "menus.toml"
    store = ConfigVersionStore(tmp_path)
    for text in ("a", "b", "c"):
        _write(cfg, text); store.snapshot(cfg)
    assert store.delete_key("menus.toml") == 3
    assert store.list_versions(cfg) == []


def test_purge_keeps_newest_and_caps_count(tmp_path: Path) -> None:
    cfg = tmp_path / "dictionary.toml"
    store = ConfigVersionStore(tmp_path)
    for i in range(5):
        _write(cfg, f"v{i}"); store.snapshot(cfg)   # 5 versions
    # Keep the 2 most-recent — drop the older 3.
    assert store.purge(max_versions=2) == 3
    assert [v.version_num for v in store.list_versions(cfg)] == [5, 4]
    # Re-purge with the same cap is a no-op (already within policy).
    assert store.purge(max_versions=2) == 0
    # No knobs set → no-op.
    assert store.purge() == 0


def test_purge_age_never_empties_a_key(tmp_path: Path) -> None:
    cfg = tmp_path / "connectors.toml"
    store = ConfigVersionStore(tmp_path)
    _write(cfg, "old"); v1 = store.snapshot(cfg)
    _write(cfg, "new"); v2 = store.snapshot(cfg)
    assert v1 is not None and v2 is not None
    # Backdate every row far past any cutoff; age purge must still keep the newest one.
    with store._conn() as c:
        c.execute("UPDATE config_version SET created_at='2000-01-01T00:00:00+00:00'")
    assert store.purge(max_age_days=30) == 1
    assert [v.version_num for v in store.list_versions(cfg)] == [2]


def test_purge_config_versions_callable(tmp_path: Path) -> None:
    # The NomaFlow callable reconstructs a store from settings.connectors.config_path and purges.
    from liberty.jobs.maintenance import purge_config_versions
    cfg = tmp_path / "screens.toml"
    store = ConfigVersionStore(tmp_path)
    for i in range(4):
        _write(cfg, f"v{i}"); store.snapshot(cfg)
    settings = SimpleNamespace(connectors=SimpleNamespace(config_path=cfg))
    removed = purge_config_versions(settings, ctx=SimpleNamespace(run_id="r1"), max_versions=1, max_age_days=0)
    assert removed == 3
    assert [v.version_num for v in ConfigVersionStore(tmp_path).list_versions(cfg)] == [4]


def test_upgrade_history_install_then_upgrade(tmp_path: Path) -> None:
    from liberty.versioning import record_upgrade_if_changed
    store = ConfigVersionStore(tmp_path)
    assert store.current_app_version() is None
    e1 = record_upgrade_if_changed(store, "7.0.26")
    assert e1 is not None and e1["kind"] == "install" and e1["from_version"] is None and e1["to_version"] == "7.0.26"
    # Same version on the next start → no-op.
    assert record_upgrade_if_changed(store, "7.0.26") is None
    # A version bump → an upgrade row.
    e2 = record_upgrade_if_changed(store, "7.0.27")
    assert e2 is not None and e2["kind"] == "upgrade" and e2["from_version"] == "7.0.26" and e2["to_version"] == "7.0.27"
    assert store.current_app_version() == "7.0.27"
    ups = store.list_upgrades()
    assert [u["to_version"] for u in ups] == ["7.0.27", "7.0.26"]   # newest first
    assert ups[0]["kind"] == "upgrade" and ups[1]["kind"] == "install" and ups[0]["component"] == "framework"
    # Survives a fresh store over the same dir.
    assert ConfigVersionStore(tmp_path).current_app_version() == "7.0.27"


def test_upgrade_history_components_are_independent(tmp_path: Path) -> None:
    """Framework + apps versions are tracked separately; ``None`` apps version is skipped."""
    from liberty.versioning import record_upgrades_if_changed
    store = ConfigVersionStore(tmp_path)
    # First start: framework installed, apps not installed (None).
    rec = record_upgrades_if_changed(store, {"framework": "7.0.27", "apps": None})
    assert [r["component"] for r in rec] == ["framework"]
    # Later: apps installed, framework unchanged → only an apps install row.
    rec = record_upgrades_if_changed(store, {"framework": "7.0.27", "apps": "1.0.0"})
    assert [(r["component"], r["kind"]) for r in rec] == [("apps", "install")]
    # Apps upgrade alone.
    rec = record_upgrades_if_changed(store, {"framework": "7.0.27", "apps": "1.0.1"})
    assert [(r["component"], r["kind"], r["from_version"], r["to_version"]) for r in rec] == [("apps", "upgrade", "1.0.0", "1.0.1")]
    assert store.current_app_version("framework") == "7.0.27"
    assert store.current_app_version("apps") == "1.0.1"
