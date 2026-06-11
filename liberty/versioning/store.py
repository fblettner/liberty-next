"""Filesystem snapshot store for the TOML config files — the foundation of config versioning
(history / diff / restore / export).

Design (mirrors nomaubl's ``FileVersionService``, adapted to liberty's colocated TOML configs):

* Every config **save** snapshots the PRIOR file content *before* overwriting it, so history is
  lossless and any save is reversible. Snapshots are **content-addressed**: a SHA-256 of the bytes,
  and a snapshot is skipped when it equals the latest version (no-op on unchanged content).
* ``version_num`` is a per-file monotonic 1-based counter.
* Snapshots are plain files under a ``.versions/`` dir next to the config — portable, git-friendly,
  no DB pool required. A small **SQLite** index (``.versions/index.db``) holds the metadata.
* Restore snapshots the *current* live file first (as a ``restore`` version) then writes the chosen
  version back, so you can always undo a restore. The caller reloads config afterwards.

This is file/section-level versioning; the screen+dependency *bundle* layer builds on top of it.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_version (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  rel_path      TEXT    NOT NULL,
  version_num   INTEGER NOT NULL,
  snapshot_path TEXT    NOT NULL,
  file_size     INTEGER NOT NULL,
  checksum      TEXT    NOT NULL,
  source        TEXT    NOT NULL,
  comment       TEXT,
  who           TEXT,
  created_at    TEXT    NOT NULL,
  UNIQUE(rel_path, version_num)
);
CREATE INDEX IF NOT EXISTS ix_config_version_path ON config_version(rel_path, version_num DESC);
"""


@dataclass(slots=True)
class ConfigVersion:
    """One stored snapshot's metadata (the bytes live on disk at ``snapshot_path``)."""

    id: int
    rel_path: str
    version_num: int
    snapshot_path: str
    file_size: int
    checksum: str
    source: str       # manual (a save) | restore | import | seed
    comment: str | None
    who: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "file": self.rel_path, "version": self.version_num,
            "size": self.file_size, "checksum": self.checksum, "source": self.source,
            "comment": self.comment, "who": self.who, "created_at": self.created_at,
        }


class ConfigVersionStore:
    """Snapshot history for the config files under *base_dir* (the dir holding connectors.toml,
    dictionary.toml, screens.toml, …). Thread-unsafe by design — wrap calls behind the single-process
    web layer; SQLite's own locking covers concurrent reads."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).resolve()
        self.vdir = self.base / ".versions"
        self.vdir.mkdir(parents=True, exist_ok=True)
        self._db = self.vdir / "index.db"
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ── internals ──────────────────────────────────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db)
        c.row_factory = sqlite3.Row
        return c

    def _rel(self, path: str | Path) -> str:
        """The file's key — its path relative to *base* (POSIX), or the bare name if it lives
        elsewhere (so an absolute path outside the config dir still gets a stable key)."""
        p = Path(path).resolve()
        try:
            return p.relative_to(self.base).as_posix()
        except ValueError:
            return p.name

    @staticmethod
    def _row_to_version(r: sqlite3.Row) -> ConfigVersion:
        return ConfigVersion(
            id=r["id"], rel_path=r["rel_path"], version_num=r["version_num"],
            snapshot_path=r["snapshot_path"], file_size=r["file_size"], checksum=r["checksum"],
            source=r["source"], comment=r["comment"], who=r["who"], created_at=r["created_at"],
        )

    # ── snapshot ───────────────────────────────────────────────────────────────────────────
    def snapshot(
        self, path: str | Path, *, source: str = "manual", who: str | None = None,
        comment: str | None = None,
    ) -> ConfigVersion | None:
        """Capture *path*'s current bytes as a new version. Returns the version, or ``None`` when the
        file doesn't exist yet (nothing to snapshot) or its content equals the latest snapshot
        (content-addressed no-op)."""
        path = Path(path)
        if not path.exists() or not path.is_file():
            return None
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        rel = self._rel(path)
        with self._conn() as c:
            last = c.execute(
                "SELECT version_num, checksum FROM config_version WHERE rel_path=? "
                "ORDER BY version_num DESC LIMIT 1",
                (rel,),
            ).fetchone()
            if last is not None and last["checksum"] == checksum:
                return None  # unchanged since the last version — skip
            num = (last["version_num"] + 1) if last is not None else 1
            snap = self.vdir / f"{rel}.v{num}"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_bytes(data)
            created = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur = c.execute(
                "INSERT INTO config_version"
                "(rel_path, version_num, snapshot_path, file_size, checksum, source, comment, who, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rel, num, str(snap), len(data), checksum, source, comment, who, created),
            )
            return ConfigVersion(
                id=int(cur.lastrowid), rel_path=rel, version_num=num, snapshot_path=str(snap),
                file_size=len(data), checksum=checksum, source=source, comment=comment, who=who,
                created_at=created,
            )

    # ── reads ──────────────────────────────────────────────────────────────────────────────
    def list_versions(self, path: str | Path | None = None) -> list[ConfigVersion]:
        """All versions for *path* (newest first); every version across all files when *path* is None."""
        with self._conn() as c:
            if path is None:
                rows = c.execute(
                    "SELECT * FROM config_version ORDER BY created_at DESC, id DESC"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM config_version WHERE rel_path=? ORDER BY version_num DESC",
                    (self._rel(path),),
                ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def get(self, version_id: int) -> ConfigVersion | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM config_version WHERE id=?", (version_id,)).fetchone()
        return self._row_to_version(r) if r is not None else None

    def content(self, version_id: int) -> bytes | None:
        """The stored bytes of a version (for diff / preview / download)."""
        v = self.get(version_id)
        if v is None:
            return None
        snap = Path(v.snapshot_path)
        return snap.read_bytes() if snap.exists() else None

    def live_path(self, rel_path: str) -> Path:
        """The live config file a ``rel_path`` maps back to (``base / rel_path``)."""
        return self.base / rel_path

    # ── restore ────────────────────────────────────────────────────────────────────────────
    def restore(self, version_id: int, *, who: str | None = None) -> Path | None:
        """Write a version's content back to its live file. Snapshots the CURRENT live file first
        (``source='restore'``) so the restore itself is undoable. Returns the live path (the caller
        reloads config), or ``None`` if the version / its snapshot is missing."""
        v = self.get(version_id)
        if v is None:
            return None
        snap = Path(v.snapshot_path)
        if not snap.exists():
            return None
        live = self.live_path(v.rel_path)
        # Preserve whatever is live right now as a new version before overwriting it.
        self.snapshot(live, source="restore", who=who, comment=f"before restore to v{v.version_num}")
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_bytes(snap.read_bytes())
        return live
