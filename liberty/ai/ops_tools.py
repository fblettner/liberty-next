"""Operations tools — the running server's state for an administrator's assistant:
``jobs_status`` (the nomaflow jobs with their schedule, last run, next fire, and a job's
recent runs), ``server_log`` (the in-memory log tail the technical dashboard shows) and
``integrity_checks`` (the configuration cross-reference checks of Settings → Integrity).

Opt-in (``[ai] ops_tools = true``): the log can carry what the server logged (an SQL
statement under ``debug_sql``, a stack trace), so an operator enables these for an
installation whose chat users are administrators. Everything reads ``app.state`` at call
time; nothing writes, nothing fires a job."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from liberty.ai.tools import Tool, tool

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def build_ops_tools(state: Callable[[], Any]) -> list[Tool]:
    async def jobs_status(job_id: str | None = None, runs: int = 10) -> dict[str, Any]:
        """The nomaflow jobs: schedule, enabled, whether the scheduler holds them, the last
        run (state, times, rows, error) and the next fire. With *job_id*, that job alone plus
        its most recent runs. Call it for "is the nightly job running", "when does X fire
        next", "did anything fail today".

        Args:
            job_id: one job to detail (its recent runs are included); omit for all jobs.
            runs: how many recent runs to list for *job_id* (default 10, at most 100).
        """
        st = state()
        comps = getattr(st, "jobs", None)
        if comps is None:
            return {"enabled": False, "note": "nomaflow is not running on this server ([jobs] enabled / pool)"}
        from liberty.web.jobs import _job_summary, _latest_runs_by_job

        registry, scheduler = comps.registry, comps.scheduler
        scheduled_ids = set(scheduler.scheduled_job_ids)
        in_flight = scheduler.in_flight_job_ids
        next_fires = scheduler.next_fire_times
        last_runs = await _latest_runs_by_job(comps.db)
        jobs = [j for j in registry.jobs() if job_id is None or j.id == job_id]
        if job_id is not None and not jobs:
            raise KeyError(f"no job {job_id!r} — jobs_status() without an id lists them")
        out: dict[str, Any] = {"enabled": True, "jobs": [
            _job_summary(j, scheduled_ids=scheduled_ids, in_flight_ids=in_flight, last_run=last_runs.get(j.id), next_fires=next_fires)
            for j in jobs
        ]}
        if job_id is not None:
            from sqlalchemy import select
            from liberty.jobs.models import JobRun

            stmt = select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.started_at.desc()).limit(max(1, min(int(runs), 100)))
            async with comps.db.session() as session:
                rows = (await session.execute(stmt)).scalars().all()
            out["recent_runs"] = [
                {"run_id": r.id, "state": r.state, "trigger": r.trigger_kind, "triggered_by": r.triggered_by,
                 "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at), "rows_affected": r.rows_affected,
                 **({"error": r.error_message} if r.error_message else {}),
                 "steps": [{"name": s.step_name, "state": s.state, "attempt": s.attempt, **({"error": s.error_message} if s.error_message else {})} for s in r.step_runs]}
                for r in rows
            ]
        return out

    def server_log(level: str = "WARNING", contains: str | None = None, limit: int = 100) -> dict[str, Any]:
        """The server's recent log lines (the in-memory tail, newest last). Call it to see why
        something failed on the server side: an error at startup, a connector that could not
        connect, a job step's exception.

        Args:
            level: the minimum level to include — DEBUG, INFO, WARNING (default), ERROR, CRITICAL.
            contains: only lines whose logger name or message contains this text (case-insensitive).
            limit: at most this many lines, the most recent ones (default 100, at most 500).
        """
        st = state()
        sio = getattr(st, "sio", None)
        logs = getattr(sio, "logs", None)
        if logs is None:
            return {"lines": [], "note": "no log buffer on this server"}
        floor = _LEVELS.get((level or "WARNING").upper(), 30)
        needle = (contains or "").lower()
        picked = []
        for e in logs.snapshot():
            if _LEVELS.get(str(e.level).upper(), 0) < floor:
                continue
            if needle and needle not in f"{e.name} {e.message}".lower():
                continue
            picked.append(e.to_dict())
        cap = max(1, min(int(limit), 500))
        return {"lines": picked[-cap:], "total_matching": len(picked)}

    def integrity_checks(severity: str | None = None) -> dict[str, Any]:
        """Run the configuration integrity checks (Settings → Integrity): broken references
        between menus, screens, queries, dictionary items, dashboards and charts ("error"),
        and unused or orphaned entities ("warning"). Call it when something is "missing" or
        "not found" in the UI, or before a deployment.

        Args:
            severity: "error" or "warning" to keep one kind; omit for both.
        """
        from liberty.web.integrity import check_integrity

        issues = check_integrity(state())
        if severity:
            issues = [i for i in issues if i.severity == severity.lower()]
        return {
            "errors": sum(1 for i in issues if i.severity == "error"),
            "warnings": sum(1 for i in issues if i.severity == "warning"),
            "issues": [{"severity": i.severity, "category": i.category, "message": i.message} for i in issues],
        }

    return [
        tool(jobs_status, summary_keys=("job_id",)),
        tool(server_log, summary_keys=("level", "contains")),
        tool(integrity_checks, summary_keys=("severity",)),
    ]
