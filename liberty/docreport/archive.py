"""Copies of the generated documentation reports (C12): every implementation / technical report
built from Liberty Next → Documentation reports is kept under ``<config dir>/.reports/`` (next to
``.versions/`` and ``.promotion/``, so a backup carries it) — the file as downloaded plus a small
JSON beside it (who, when, the language, the sections, the title). The page lists them for a new
download; a copy is deleted from there (the Retention page shows their number and size).

Names are ``<YYYYMMDD-HHMMSS>-<kind>-<lang>[-n].<pdf|md>`` (UTC); only names of that shape are
served or deleted, so a request cannot reach another file.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^(\d{8}-\d{6})-([a-z]+)-([a-z]{2})(?:-\d+)?\.(pdf|md)$")


def archive_dir(settings: Any) -> Path:
    return Path(settings.connectors.config_path).parent / ".reports"


def _entry(path: Path) -> dict[str, Any]:
    m = _NAME.match(path.name)
    meta: dict[str, Any] = {}
    side = path.with_name(path.name + ".json")
    if side.is_file():
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    stamp = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc) if m else None
    return {
        "name": path.name, "kind": m.group(2) if m else None, "lang": m.group(3) if m else None,
        "format": "pdf" if path.suffix == ".pdf" else "markdown", "size": path.stat().st_size,
        "created_at": stamp.isoformat() if stamp else None,
        "title": meta.get("title"), "author": meta.get("author"), "sections": meta.get("sections") or [],
        "date_label": meta.get("date_label"),
    }


def save(settings: Any, *, kind: str, lang: str, fmt: str, body: bytes, title: str | None = None,
         author: str | None = None, sections: list[str] | None = None, date_label: str | None = None) -> dict[str, Any]:
    """Keep *body* (a rendered report) and its description; the new entry."""
    d = archive_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ext = "pdf" if fmt == "pdf" else "md"
    path, n = d / f"{stamp}-{kind}-{lang}.{ext}", 1
    while path.exists():                       # two builds in the same second
        path, n = d / f"{stamp}-{kind}-{lang}-{n}.{ext}", n + 1
    path.write_bytes(body)
    meta = {"title": title, "author": author, "sections": list(sections or []), "date_label": date_label}
    path.with_name(path.name + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return _entry(path)


def entries(settings: Any) -> list[dict[str, Any]]:
    """The kept copies, newest first."""
    d = archive_dir(settings)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and _NAME.match(p.name)]
    return [_entry(p) for p in sorted(files, key=lambda p: p.name, reverse=True)]


def path_of(settings: Any, name: str) -> Path | None:
    """The copy *name*, or None when the name is not a report's or the file is gone."""
    if not _NAME.match(name):
        return None
    p = archive_dir(settings) / name
    return p if p.is_file() else None


def delete(settings: Any, name: str) -> bool:
    p = path_of(settings, name)
    if p is None:
        return False
    p.unlink()
    p.with_name(p.name + ".json").unlink(missing_ok=True)
    return True


def usage(settings: Any) -> dict[str, int]:
    """``{files, bytes}`` of the kept copies (the Retention page)."""
    items = entries(settings)
    return {"files": len(items), "bytes": sum(i["size"] for i in items)}
