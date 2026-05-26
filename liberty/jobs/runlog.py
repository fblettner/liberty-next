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

# Logger namespaces the RunLogHandler is attached to. ``liberty`` is registered
# by :func:`install`. Plugins call :func:`register_namespace` at import time to
# opt their own logger tree in (e.g. ``nomasx1.security`` lives outside the
# ``liberty.*`` tree, so without registration its log records never reach the
# run buffer). The handler instance is module-level so a late registration
# (after :func:`install` ran) can still find it.
_registered_namespaces: set[str] = set()
_handler: "RunLogHandler | None" = None


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
    is left alone.

    Plugin loggers (e.g. ``nomasx1.security``) live OUTSIDE the ``liberty.*``
    tree, so without registration their records never reach the run buffer.
    Use :func:`register_namespace` from plugin import code to attach the
    handler to additional namespaces."""
    global _handler
    if _handler is not None:
        # Re-running install (e.g. lifespan re-init) — just rebind the
        # registered namespaces against the existing handler.
        _attach_to_namespace("liberty")
        return _handler
    _handler = RunLogHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s — %(message)s", datefmt="%H:%M:%S",
    ))
    _attach_to_namespace("liberty")
    return _handler


def registered_namespaces() -> tuple[str, ...]:
    """The current set of registered logger namespaces — used by the runner to
    raise + restore the right loggers' levels when a per-run DEBUG override is
    active. Returns a snapshot tuple so callers don't need to lock the set."""
    return tuple(sorted(_registered_namespaces))


def register_namespace(name: str) -> None:
    """Attach the :class:`RunLogHandler` to the logger *name* so records emitted
    from that tree are captured into the active run's buffer.

    Idempotent — calling twice with the same name is a no-op. Safe to call
    before :func:`install` runs (the handler is attached lazily when install
    happens — registrations made earlier are picked up then).

    Use from plugin import code, e.g. in ``plugins/nomasx1/__init__.py``::

        from liberty.jobs.runlog import register_namespace
        register_namespace("nomasx1")

    so every ``nomasx1.<submodule>`` logger feeds the run buffer alongside
    ``liberty.<submodule>`` loggers.
    """
    _attach_to_namespace(name)


def _attach_to_namespace(name: str) -> None:
    """Internal — bind the handler to logger *name* and pin its level so a
    deployment that left the namespace at default WARNING doesn't silently
    drop INFO records. Handles late registration (install hasn't run yet)
    by remembering the name; install picks it up when the handler exists."""
    _registered_namespaces.add(name)
    lg = logging.getLogger(name)
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)
    if _handler is None:
        return  # install() will rebind when it runs
    # Idempotent attach.
    if any(isinstance(h, RunLogHandler) for h in lg.handlers):
        return
    lg.addHandler(_handler)


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
