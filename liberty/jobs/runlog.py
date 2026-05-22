"""Per-run log capture — so a job's log is viewable in the UI, not just stdout.

Scheduled jobs run unattended; "tail the server stdout" is not an answer. This
module captures every log line emitted *during a run* and tags it with that
run's id, so the UI can show a run's log (live while it runs, durably after).

How it works:

* :data:`_current_run_id` — a :class:`ContextVar` holding the active run id.
  The runner sets it for the duration of :meth:`JobRunner.run`. asyncio copies
  the context into every task it spawns, so the step-executor tasks (created by
  ``asyncio.wait_for``) inherit the run id — their log lines are captured too.
* :class:`RunLogHandler` — a stdlib logging handler attached to the ``liberty``
  logger. On each record it reads the contextvar; if a run is active it appends
  the formatted line to that run's in-memory ring buffer.
* The runner reads the buffer at finalize, writes it to ``nomaflow_run_logs``
  (durable), and drops the in-memory buffer.

The logs endpoint serves the **in-memory buffer** while a run is active (so a
poll sees lines the instant they're logged, even mid-step) and the **DB row**
once the run has finished. Single-process assumption — the buffer lives in the
worker that ran the job (consistent with nomaflow's current single-scheduler
model; see PHASE13 §11 #2).
"""

from __future__ import annotations

import logging
from collections import deque
from contextvars import ContextVar, Token
from threading import Lock

# The active run id, or None outside a run. Set by JobRunner.run via
# set_run_context / reset_run_context.
_current_run_id: ContextVar[str | None] = ContextVar("nomaflow_run_id", default=None)

# Per-run ring buffers. Bounded so a chatty / runaway job can't exhaust memory;
# the oldest lines drop. A guard lock keeps the dict mutation safe against the
# logging handler firing from an executor thread (run_in_executor steps).
_MAX_LINES = 5000
_MAX_PERSIST_CHARS = 256 * 1024  # cap the text written to the DB

_buffers: dict[str, deque[str]] = {}
_lock = Lock()


def set_run_context(run_id: str) -> Token:
    """Bind *run_id* as the active run for the current async context. Returns a
    token to pass to :func:`reset_run_context`."""
    return _current_run_id.set(run_id)


def reset_run_context(token: Token) -> None:
    """Undo a :func:`set_run_context` — restores whatever run id (or None) was
    active before."""
    _current_run_id.reset(token)


def current_run_id() -> str | None:
    return _current_run_id.get()


class RunLogHandler(logging.Handler):
    """Captures log records emitted within a run's context into that run's buffer.

    Attach once to the ``liberty`` logger (see :func:`install`). Records emitted
    outside any run (``_current_run_id`` is None) are ignored — this handler
    only does per-run capture; the stdout handler from ``main._setup_app_logging``
    still prints everything."""

    def emit(self, record: logging.LogRecord) -> None:
        run_id = _current_run_id.get()
        if run_id is None:
            return
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - never let logging crash a run
            return
        with _lock:
            buf = _buffers.get(run_id)
            if buf is None:
                buf = deque(maxlen=_MAX_LINES)
                _buffers[run_id] = buf
            buf.append(line)


def install() -> RunLogHandler:
    """Attach a :class:`RunLogHandler` to the ``liberty`` logger (idempotent —
    a second call returns the already-installed handler). Called from the
    nomaflow lifespan wiring.

    A handler only sees a record if the *logger* first passes it (logger level
    gate). So this also pins the ``liberty`` logger to at most INFO — otherwise
    a deployment started without :func:`liberty.main._setup_app_logging` (e.g.
    a bare ``uvicorn liberty.main:asgi_app``, or the test client) would leave
    the logger at its WARNING-ish default and the run-log buffer would capture
    nothing. It only ever *lowers* the level, never raises it — a DEBUG setting
    is left alone."""
    lg = logging.getLogger("liberty")
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)
    for h in lg.handlers:
        if isinstance(h, RunLogHandler):
            return h
    handler = RunLogHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s — %(message)s", datefmt="%H:%M:%S",
    ))
    lg.addHandler(handler)
    return handler


def run_logs(run_id: str) -> str | None:
    """The live in-memory log text for *run_id* — the joined ring buffer — or
    None if no buffer exists (the run never logged, or its buffer was already
    flushed + dropped at finalize)."""
    with _lock:
        buf = _buffers.get(run_id)
        if buf is None:
            return None
        return "\n".join(buf)


def finish_run(run_id: str) -> str:
    """Pop *run_id*'s buffer — return its text (capped at the persist limit) and
    drop it from memory. Called by the runner at finalize, just before writing
    the text to ``nomaflow_run_logs``."""
    with _lock:
        buf = _buffers.pop(run_id, None)
    if buf is None:
        return ""
    text = "\n".join(buf)
    if len(text) > _MAX_PERSIST_CHARS:
        # Keep the tail — the end of a log is where the failure is.
        text = "…(truncated)…\n" + text[-_MAX_PERSIST_CHARS:]
    return text


def discard_run(run_id: str) -> None:
    """Drop *run_id*'s buffer without returning it — a safety-net cleanup."""
    with _lock:
        _buffers.pop(run_id, None)
