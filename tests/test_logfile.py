"""The server log on disk (:mod:`liberty.logfile`): the JSON-lines file, repeats collapsed,
rotation, levels per module, the search."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from liberty.logfile import RepeatCollapser, ServerLogHandler, apply_levels, as_text, install, log_files, log_path, read_entries, uninstall


def _rec(name: str, msg: str, *, level=logging.INFO, at: float = 1000.0) -> logging.LogRecord:
    r = logging.makeLogRecord({"name": name, "levelno": level, "levelname": logging.getLevelName(level), "msg": msg, "created": at})
    return r


def test_repeats_collapse_within_the_window() -> None:
    out: list[str] = []
    c = RepeatCollapser(window=60)
    emit = lambda r: out.append(r.getMessage())  # noqa: E731
    for i in range(5):
        c.process(_rec("liberty.db", "connection refused", at=1000 + i), emit)
    c.process(_rec("liberty.db", "connected", at=1010), emit)
    c.process(_rec("liberty.db", "connected", at=2000), emit)      # same text, outside the window: kept
    assert out == ["connection refused", "(previous message repeated 4 times)", "connected", "connected"]


def test_the_file_rotation_and_the_search(tmp_path) -> None:
    path = tmp_path / ".logs" / "liberty.log"
    h = ServerLogHandler(path, max_mb=1, backups=2)
    lg = logging.getLogger("liberty.test.logfile")
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    lg.addHandler(h)
    try:
        lg.info("job nightly started")
        lg.warning("pool jdedwards unreachable")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            lg.exception("step failed")
        for _ in range(3):
            lg.info("same line")
        lg.debug("debug detail %s", 7)
    finally:
        lg.removeHandler(h)
        h.close()
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    assert first["logger"] == "liberty.test.logfile" and first["msg"] == "job nightly started" and first["level"] == "INFO"
    assert any("repeated 2 times" in json.loads(x)["msg"] for x in lines)
    files = log_files(path)
    every, scanned = read_entries(files)
    assert [e["msg"] for e in every][:2] == ["debug detail 7", "(previous message repeated 2 times)"]   # newest first
    assert scanned == len(lines)
    assert [e["msg"] for e in read_entries(files, level="WARNING")[0]] == ["step failed", "pool jdedwards unreachable"]
    assert [e["msg"] for e in read_entries(files, q="BOOM")[0]] == ["step failed"]                    # the exception text
    assert read_entries(files, module="liberty.test")[0] and not read_entries(files, module="liberty.tes")[0]
    assert len(read_entries(files, limit=2)[0]) == 2
    assert read_entries(files, since="2999-01-01")[0] == []
    assert "step failed" in as_text(read_entries(files, q="boom")[0][0]) and "RuntimeError" in as_text(read_entries(files, q="boom")[0][0])


def test_install_levels_and_paths(tmp_path) -> None:
    settings = SimpleNamespace(
        app=SimpleNamespace(log_file=None, log_file_max_mb=1, log_file_backups=1, log_levels={"liberty.test.levels": "DEBUG"}),
        connectors=SimpleNamespace(config_path=str(tmp_path / "config" / "connectors.toml")),
    )
    assert log_path(settings) == tmp_path / "config" / ".logs" / "liberty.log"
    h = install(settings)
    try:
        assert logging.getLogger("liberty.test.levels").level == logging.DEBUG
        assert h in logging.getLogger("liberty").handlers and h in logging.getLogger().handlers
        logging.getLogger("liberty.test.levels").debug("visible at debug")
    finally:
        uninstall(h)
    assert h not in logging.getLogger("liberty").handlers
    assert "visible at debug" in (tmp_path / "config" / ".logs" / "liberty.log").read_text()
    assert apply_levels({"liberty.test.levels": ""}) == {"liberty.test.levels": "NOTSET"}
    with pytest.raises(ValueError):
        apply_levels({"x": "LOUD"})
    settings.app.log_file = ""
    assert log_path(settings) is None and install(settings) is None
