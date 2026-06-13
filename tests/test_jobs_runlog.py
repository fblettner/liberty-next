"""Tests for :mod:`liberty.jobs.runlog` — per-run log capture.

The contract: a log line emitted while a run id is bound to the async context
lands in that run's buffer; lines emitted outside any run don't; finish_run
returns the text and drops the buffer.
"""

from __future__ import annotations

import logging

import pytest

from liberty.jobs import runlog


@pytest.fixture(autouse=True)
def _clean_buffers():
    """Each test starts with no buffers + the handler installed + INFO level."""
    runlog.install()
    logging.getLogger("liberty").setLevel(logging.INFO)
    yield
    for rid in ("run-A", "run-B", "run-ctx", "run-warn"):
        runlog.discard_run(rid)


def test_captures_lines_within_a_run_context() -> None:
    log = logging.getLogger("liberty.jobs.test")
    log.info("emitted before any run")           # no context → not captured
    assert runlog.run_logs("run-A") is None

    token = runlog.set_run_context("run-A")
    try:
        log.info("first inside")
        log.warning("second inside")
    finally:
        runlog.reset_run_context(token)

    log.info("emitted after the run")            # context reset → not captured

    text = runlog.run_logs("run-A")
    assert text is not None
    assert "first inside" in text
    assert "second inside" in text
    assert "before any run" not in text
    assert "after the run" not in text


def test_finish_run_returns_text_and_drops_buffer() -> None:
    log = logging.getLogger("liberty.jobs.test")
    token = runlog.set_run_context("run-B")
    try:
        log.info("line one")
        log.info("line two")
    finally:
        runlog.reset_run_context(token)

    final = runlog.finish_run("run-B")
    assert "line one" in final and "line two" in final
    # Buffer is gone after finish_run — the endpoint reads the DB from here on.
    assert runlog.run_logs("run-B") is None
    # A second finish_run is a harmless empty string.
    assert runlog.finish_run("run-B") == ""


def test_finish_run_for_unknown_run_is_empty() -> None:
    assert runlog.finish_run("never-existed") == ""


def test_run_level_gate_drops_below_minimum() -> None:
    """A run gated to WARNING keeps warnings/errors but drops INFO lines — without
    touching the global logger level (so a parallel INFO run is unaffected)."""
    log = logging.getLogger("liberty.jobs.test")
    token = runlog.set_run_context("run-warn")
    runlog.set_run_level("run-warn", logging.WARNING)
    try:
        log.info("an info progress line")
        log.warning("a real warning")
        log.error("an error too")
    finally:
        runlog.reset_run_context(token)

    text = runlog.run_logs("run-warn")
    assert text is not None
    assert "a real warning" in text and "an error too" in text
    assert "an info progress line" not in text
    # The global logger level is untouched — still INFO from the fixture.
    assert logging.getLogger("liberty").level == logging.INFO
    # finish_run clears the per-run level too (no leak across runs reusing an id).
    runlog.finish_run("run-warn")
    assert runlog._run_levels.get("run-warn") is None


def test_current_run_id_reflects_context() -> None:
    assert runlog.current_run_id() is None
    token = runlog.set_run_context("run-ctx")
    try:
        assert runlog.current_run_id() == "run-ctx"
    finally:
        runlog.reset_run_context(token)
    assert runlog.current_run_id() is None


def test_install_is_idempotent() -> None:
    h1 = runlog.install()
    h2 = runlog.install()
    assert h1 is h2
    # Exactly one RunLogHandler on the liberty logger.
    handlers = [h for h in logging.getLogger("liberty").handlers
                if isinstance(h, runlog.RunLogHandler)]
    assert len(handlers) == 1
