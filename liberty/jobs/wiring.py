"""Lifespan wiring helpers — keep main.py's nomaflow integration ≤ a handful
of lines.

The shape: :func:`build_nomaflow` takes the things the lifespan already has
(settings, connector registry, sio layer) and returns a started + ready
:class:`JobRegistry` / :class:`JobRunner` / :class:`JobScheduler` triple.
The lifespan then just hangs those on ``app.state.*`` and calls the matching
:func:`shutdown_nomaflow` on the way out.

The Socket.IO broadcaster bridge (Phase 9 ``/technical`` integration) is here
too as :class:`SioJobBroadcaster` — it adapts ``LibertySio.sio.emit`` to the
:class:`~liberty.jobs.runner.Broadcaster` Protocol that ``JobRunner`` expects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from liberty.config import Settings
from liberty.connectors import ConnectorRegistry
from liberty.jobs.db import JobDatabase
from liberty.jobs.registry import JobRegistry, load_jobs
from liberty.jobs.runner import Broadcaster, JobRunner
from liberty.jobs.scheduler import JobScheduler
from liberty.jobs.schema import StepType
from liberty.jobs.steps import SqlCopyExecutor, SqlQueryExecutor, StepExecutor

_log = logging.getLogger(__name__)


# Socket.IO event prefix for nomaflow broadcasts. Keeps related events grouped
# in the client (the technical dashboard filters on this prefix to pick up
# every nomaflow signal without enumerating each event type).
_SIO_EVENT_PREFIX = "nomaflow."


class SioJobBroadcaster:
    """Adapts :class:`LibertySio` ``sio.emit`` to the
    :class:`~liberty.jobs.runner.Broadcaster` Protocol.

    Every nomaflow broadcast goes to the ``dashboard`` room (the same room the
    technical-dashboard pane subscribes to) with the event name prefixed by
    ``nomaflow.``. A broken Socket.IO layer doesn't propagate failures back
    into the runner / scheduler — we log + swallow so a dashboard outage
    can't take down the job system.
    """

    def __init__(self, sio_layer: Any, *, room: str = "dashboard") -> None:
        # Typed as Any to avoid pulling LibertySio into the import graph at
        # module load time (it pulls socket.io which pulls a lot more); the
        # runtime check is the duck-typed ``sio.emit`` call below.
        self._sio = getattr(sio_layer, "sio", None)
        self._room = room

    async def broadcast(self, event: str, payload: dict) -> None:
        if self._sio is None:
            return
        try:
            await self._sio.emit(f"{_SIO_EVENT_PREFIX}{event}", payload, room=self._room)
        except Exception:  # pragma: no cover - belt-and-braces; never let SIO take us down
            _log.exception("nomaflow.broadcast: sio.emit failed for event %r", event)


@dataclass
class NomaflowComponents:
    """The three nomaflow objects the lifespan parks on ``app.state``.

    Kept as a dataclass (not a tuple) so adding a fourth thing later doesn't
    break callers, and so the lifespan can do ``app.state.jobs = result``
    in one assignment instead of three."""

    registry: JobRegistry
    runner: JobRunner
    scheduler: JobScheduler
    db: JobDatabase


def build_executors(connectors: ConnectorRegistry) -> dict[StepType, StepExecutor]:
    """The default executor wiring — one per implemented :class:`StepType`.

    Chunk 2a/b ships ``sql_query`` and ``sql_copy``. The remaining types
    (``python``, ``ldap_sync``, ``http``) get added here when their executors
    land; until then, jobs that reference them parse fine but fail at run time
    with a clear "no executor registered" message from ``JobRunner``."""
    return {
        StepType.SQL_QUERY: SqlQueryExecutor(connectors),
        StepType.SQL_COPY: SqlCopyExecutor(connectors),
    }


async def build_nomaflow(
    settings: Settings,
    connectors: ConnectorRegistry,
    *,
    sio_layer: Any | None = None,
) -> NomaflowComponents | None:
    """Build the nomaflow stack from a (settings, connectors) pair, or return
    None if the deployment isn't carrying enough to start it.

    Returns None — degrades gracefully without raising — when:

    * The configured pool (``[jobs] pool``, default ``"default"``) doesn't
      exist in the connector registry. Common in tests + API-only deployments
      that don't carry any DB pools.
    * ``[jobs] enabled = false``. The components are loaded all the way
      through but the scheduler isn't started — admins can still hit
      ``GET /admin/jobs`` to inspect the catalogue, just no fires happen.

    Sequencing on the success path:

    1. Load :class:`JobRegistry` from ``settings.jobs.config_path``.
    2. Build a :class:`JobDatabase` on the configured pool.
    3. ``create_schema()`` on the JobDatabase — idempotent; means a fresh
       install can boot without a prior ``liberty-admin init-db`` step
       creating the nomaflow tables (init-db still creates them; this is the
       belt-and-braces for "operator forgot").
    4. Build a :class:`JobRunner` with the default executor map + an optional
       :class:`SioJobBroadcaster`.
    5. Build + start a :class:`JobScheduler` (recovery sweep runs here; cron
       triggers register).

    The lifespan parks the returned :class:`NomaflowComponents` on ``app.state.jobs``
    (or None) and calls :func:`shutdown_nomaflow` on the way out.

    Idempotency: safe to call once. Don't call twice without shutting down the
    previous instance first; the second scheduler's APScheduler instance would
    happily run alongside the first and fire every job twice.
    """
    # Attach the per-run log-capture handler (idempotent). Done before the
    # scheduler starts so the very first run's log lines are captured.
    from liberty.jobs.runlog import install as install_run_log_capture
    install_run_log_capture()

    # Gate on the pool existing AND being reachable. Two failure modes:
    #   1. Pool not in the registry → ``names()`` won't list it.
    #   2. Pool listed but ``url`` is empty (an unset env var) → ``engine()``
    #      raises UnknownPoolError. Catch both → log + return None instead of
    #      taking the whole app down for a missing nomaflow dep.
    from liberty.connectors.base import UnknownPoolError

    pool_name = settings.jobs.pool
    if pool_name not in connectors.pools.names():
        _log.info(
            "nomaflow.wiring no %r pool configured — skipping nomaflow startup "
            "(set [jobs] pool to an existing pool, or add the pool to connectors.toml)",
            pool_name,
        )
        return None

    registry = load_jobs(settings.jobs.config_path)
    db = JobDatabase(connectors.pools, pool_name)
    # Self-bootstrap the schema. Idempotent (create_all skips existing tables);
    # makes "deploy and forget init-db" work as long as the pool itself exists.
    try:
        await db.create_schema()
    except UnknownPoolError as exc:
        # Pool defined but unusable (empty URL etc.). Skip nomaflow without
        # raising so the rest of the app (auth, connectors, AI) still boots.
        _log.info(
            "nomaflow.wiring pool %r is unreachable (%s) — skipping nomaflow startup",
            pool_name, exc,
        )
        return None

    broadcaster: Broadcaster | None = None
    if sio_layer is not None:
        broadcaster = SioJobBroadcaster(sio_layer)

    runner = JobRunner(
        db,
        build_executors(connectors),
        broadcaster=broadcaster,
    )
    scheduler = JobScheduler(
        registry,
        runner,
        on_dropped_fire=broadcaster.broadcast if broadcaster else None,
    )

    if settings.jobs.enabled:
        await scheduler.start()
    else:
        _log.info("nomaflow.wiring scheduler disabled via [jobs] enabled=false")

    return NomaflowComponents(registry=registry, runner=runner, scheduler=scheduler, db=db)


async def shutdown_nomaflow(components: NomaflowComponents | None) -> None:
    """Stop the scheduler cleanly. Mirror of :func:`build_nomaflow`.

    Tolerates a None components argument so the lifespan can call it
    unconditionally regardless of whether build_nomaflow returned a stack
    or skipped on a missing pool."""
    if components is None:
        return
    await components.scheduler.stop()


async def hot_reload_registry(
    components: NomaflowComponents,
    settings: Settings,
) -> JobRegistry:
    """Reload ``jobs.toml`` and reconcile the live scheduler with what's now
    in the file. Called by ``POST /admin/reload``.

    Diff strategy: compare the new registry's job ids + their schedules against
    the live scheduler. For each id:

    * Present in both with the same trigger → leave it alone (don't restart
      a cron entry that hasn't changed; APScheduler's ``next_run_time`` survives).
    * In new, not in scheduler → ``add_job`` it.
    * In scheduler, not in new → ``remove_job``.
    * In both but schedule/timezone/enabled changed → remove + re-add (APScheduler
      has ``modify_job`` / ``reschedule_job`` but the diff-and-rebuild is simpler
      and correct in every case).

    Returns the new :class:`JobRegistry` so the lifespan can swap it onto
    ``app.state.jobs.registry`` (the runner's reference also gets updated via
    :attr:`JobScheduler._registry` — kept atomic by happening in one task).
    """
    new_registry = load_jobs(settings.jobs.config_path)
    scheduler = components.scheduler

    # The scheduler's _registry attribute is what _run_or_drop reads to find
    # the Job for a fired trigger. Swap it first so any fire that arrives mid-
    # reconciliation picks up the new shape.
    scheduler._registry = new_registry  # noqa: SLF001 — same module family

    old_ids = set(scheduler.scheduled_job_ids)
    new_jobs_by_id = {j.id: j for j in new_registry.scheduled_jobs()}
    new_ids = set(new_jobs_by_id)

    # Remove jobs that disappeared from the file
    for stale in old_ids - new_ids:
        scheduler.remove_job(stale)
        _log.info("nomaflow.reload removed scheduled job %r", stale)

    # Add jobs that appeared (or re-add ones whose trigger may have changed —
    # remove first to avoid relying on add_job's idempotency for trigger changes)
    for jid in new_ids:
        if jid in old_ids:
            scheduler.remove_job(jid)
        scheduler.add_job(new_jobs_by_id[jid])
        if jid not in old_ids:
            _log.info("nomaflow.reload added scheduled job %r", jid)
        else:
            _log.info("nomaflow.reload rescheduled job %r", jid)

    # Swap the components' registry reference for callers reading
    # app.state.jobs.registry. NB: NomaflowComponents is a regular dataclass
    # (not frozen) so this assignment is supported.
    components.registry = new_registry
    return new_registry
