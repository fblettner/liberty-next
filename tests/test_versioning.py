from __future__ import annotations

from pathlib import Path

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
