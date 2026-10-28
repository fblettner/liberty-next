"""The server log on disk (C8) — what the Server log page searches and the operator downloads.

* A size-rotated JSON-lines file (``[app] log_file``, default ``<config dir>/.logs/liberty.log``;
  ``log_file_max_mb`` per file, ``log_file_backups`` older files kept) fed by the ``liberty``
  logger and the root logger (the plugins, SQLAlchemy, uvicorn, APScheduler warnings).
* Repeats collapsed: the same message from the same logger at the same level within
  :data:`REPEAT_WINDOW` seconds is written once, then "(previous message repeated N times)" when
  something else comes — a reconnect loop does not fill the file.
* Levels per module (``[app] log_levels = { "liberty.jobs" = "DEBUG", "nomasx1" = "INFO" }``),
  applied at startup and changeable at run time from the page (``POST /admin/logs/levels``).

Installed by the app's lifespan (whatever launches it — ``liberty-next``, ``uvicorn`` with
``--reload``), removed at shutdown. Reading is :func:`read_entries` (newest first, filtered).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

REPEAT_WINDOW = 60.0
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_log = logging.getLogger(__name__)


class RepeatCollapser:
    """Drops a record identical to the previous one (logger, level, message) within the window
    and, when a different record comes, first emits "(previous message repeated N times)"."""

    def __init__(self, window: float = REPEAT_WINDOW) -> None:
        self.window = window
        self._key: tuple[str, int, str] | None = None
        self._last: logging.LogRecord | None = None
        self._count = 0

    def process(self, record: logging.LogRecord, emit: Callable[[logging.LogRecord], None]) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken format still gets written as it is
            emit(record)
            return
        key = (record.name, record.levelno, msg)
        if key == self._key and self._last is not None and record.created - self._last.created <= self.window:
            self._count += 1
            self._last = record
            return
        self.flush(emit)
        self._key, self._last = key, record
        emit(record)

    def flush(self, emit: Callable[[logging.LogRecord], None]) -> None:
        if self._count and self._last is not None:
            last = self._last
            summary = logging.makeLogRecord({
                "name": last.name, "levelno": last.levelno, "levelname": last.levelname,
                "msg": f"(previous message repeated {self._count} times)", "created": last.created,
                "request_id": getattr(last, "request_id", "-"),
            })
            emit(summary)
        self._count = 0


class ServerLogHandler(RotatingFileHandler):
    """The rotating JSON-lines file, repeats collapsed."""

    def __init__(self, path: Path, *, max_mb: int, backups: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, maxBytes=max(1, max_mb) * 1024 * 1024, backupCount=max(0, backups), encoding="utf-8", delay=True)
        from liberty.observability import JsonFormatter, RequestIdFilter
        self.setFormatter(JsonFormatter())
        self.addFilter(RequestIdFilter())
        self._collapser = RepeatCollapser()

    def emit(self, record: logging.LogRecord) -> None:
        self._collapser.process(record, super().emit)

    def close(self) -> None:
        self.acquire()
        try:
            self._collapser.flush(super().emit)
        finally:
            self.release()
        super().close()


def log_path(settings: Any) -> Path | None:
    """The current log file, or None when ``[app] log_file = ""`` (off)."""
    configured = getattr(settings.app, "log_file", None)
    if configured == "":
        return None
    if configured:
        return Path(configured).expanduser()
    return Path(settings.connectors.config_path).parent / ".logs" / "liberty.log"


def log_files(path: Path) -> list[Path]:
    """The current file then the rotated ones (``.1`` newest), those that exist."""
    out = [path] if path.is_file() else []
    i = 1
    while True:
        p = path.with_name(f"{path.name}.{i}")
        if not p.is_file():
            break
        out.append(p)
        i += 1
    return out


def install(settings: Any) -> ServerLogHandler | None:
    """Attach the file to the ``liberty`` and root loggers and apply ``[app] log_levels``.
    Returns the handler (to remove at shutdown), None when the file is off."""
    apply_levels(getattr(settings.app, "log_levels", {}) or {})
    path = log_path(settings)
    if path is None:
        return None
    handler = ServerLogHandler(path, max_mb=settings.app.log_file_max_mb, backups=settings.app.log_file_backups)
    lg = logging.getLogger("liberty")
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)
    for logger in (lg, logging.getLogger()):
        if not any(isinstance(h, ServerLogHandler) for h in logger.handlers):
            logger.addHandler(handler)
    _log.info("server log → %s (%d MB × %d)", path, settings.app.log_file_max_mb, settings.app.log_file_backups)
    return handler


def uninstall(handler: ServerLogHandler | None) -> None:
    if handler is None:
        return
    for logger in (logging.getLogger("liberty"), logging.getLogger()):
        if handler in logger.handlers:
            logger.removeHandler(handler)
    handler.close()


def apply_levels(levels: dict[str, str]) -> dict[str, str]:
    """Set each named logger's level (``""`` / ``"NOTSET"`` resets it to inherit). Returns the
    levels applied; an unknown level name raises ValueError."""
    out: dict[str, str] = {}
    for name, level in levels.items():
        lv = (level or "NOTSET").upper()
        if lv != "NOTSET" and lv not in LEVELS:
            raise ValueError(f"{name}: unknown level {level!r}")
        logging.getLogger(name).setLevel(lv)
        out[name] = lv
    return out


def _iter_lines_newest_first(files: list[Path]) -> Iterator[str]:
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        yield from reversed(lines)


def read_entries(
    files: list[Path], *, level: str | None = None, module: str | None = None, q: str | None = None,
    since: str | None = None, limit: int = 500,
) -> tuple[list[dict[str, Any]], int]:
    """The log entries matching the filters, newest first, at most *limit*; with the number of
    lines scanned. *level* is a floor, *module* a logger-name prefix, *q* a case-insensitive
    substring of the message / logger / exception / request id, *since* an ISO timestamp."""
    floor = LEVELS.get((level or "").upper(), 0)
    needle = (q or "").lower()
    out: list[dict[str, Any]] = []
    scanned = 0
    for line in _iter_lines_newest_first(files):
        scanned += 1
        try:
            e = json.loads(line)
        except ValueError:
            e = {"ts": "", "level": "INFO", "logger": "-", "msg": line}
        if since and str(e.get("ts", "")) < since:
            break                                   # newest first: everything after is older
        if LEVELS.get(str(e.get("level", "")).upper(), 0) < floor:
            continue
        name = str(e.get("logger", ""))
        if module and not (name == module or name.startswith(module + ".")):
            continue
        if needle and needle not in f"{e.get('msg', '')} {name} {e.get('exc', '')} {e.get('request_id', '')}".lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out, scanned


def as_text(entry: dict[str, Any]) -> str:
    """One entry as the text line of the export."""
    line = f"{entry.get('ts', '')} {str(entry.get('level', '')):<8} {entry.get('logger', '')} [{entry.get('request_id', '-')}] — {entry.get('msg', '')}"
    return line + (f"\n{entry['exc']}" if entry.get("exc") else "")
