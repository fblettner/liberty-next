"""nomaflow's file work area — where uploads for a run land, where a ``file_load`` step writes
its rejects report, where a watched folder's files are moved once processed.

``[jobs] files_dir`` names it; by default it sits next to the config, under
``<config dir>/.nomaflow/files`` (a mounted volume on Docker, so it survives restarts).
Layout: ``<files_dir>/<job_id>/uploads/<stamp>-<name>`` for what the Run-with-parameters modal
sends, ``<files_dir>/<job_id>/<run_id>-<step>-rejects.csv`` for the reports.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from liberty.config import Settings

UPLOAD_SUFFIXES = {".xlsx", ".xlsm", ".csv", ".txt", ".tsv"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def files_dir(settings: Settings) -> Path:
    """The work area root (created on demand by the callers)."""
    if settings.jobs.files_dir is not None:
        return Path(settings.jobs.files_dir)
    return Path(settings.connectors.config_path).parent / ".nomaflow" / "files"


def safe_name(name: str) -> str:
    """A file name safe to store and to put in a URL: the basename only, odd characters
    replaced, never empty."""
    base = Path(name).name
    base = _SAFE.sub("_", base).strip("._") or "file"
    return base[:120]


def job_dir(settings: Settings, job_id: str) -> Path:
    return files_dir(settings) / safe_name(job_id)


def upload_target(settings: Settings, job_id: str, original_name: str) -> Path:
    """Where a new upload for *job_id* goes — time-stamped so two uploads of the same name
    never collide and the folder reads chronologically."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = job_dir(settings, job_id) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{stamp}-{safe_name(original_name)}"


def run_file(settings: Settings, job_id: str, name: str) -> Path | None:
    """The file *name* of a run under *job_id*'s folder, or None when the name is not a plain
    file name or the file does not exist (a traversal attempt reads as "not found")."""
    if name != safe_name(name) or "/" in name or name.startswith("."):
        return None
    p = job_dir(settings, job_id) / name
    return p if p.is_file() else None
