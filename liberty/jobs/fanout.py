"""One run per value: a job with ``for_each = "<param>"`` whose parameter holds several values
(a list, a comma-separated string, or ``"all"`` = every choice of the user parameter of that
name) is fired as one run per value, one after another, each run seeing a single value — e.g.
``nomasx1-collect-all`` for a list of applications, or for all of them on a schedule."""

from __future__ import annotations

from typing import Any

from liberty.jobs.choices import param_choices
from liberty.jobs.schema import Job

ALL = ("all", "*")


def typed(v: Any) -> Any:
    """``"3"`` → ``3`` (the values come from choice lists, i.e. strings); anything else as is."""
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit() and (s == "0" or not s.startswith("0")):
            return int(s)
        return s
    return v


def _raw(job: Job, params_override: dict[str, Any] | None) -> Any:
    name = job.for_each or ""
    return (params_override or {}).get(name, job.params.get(name))


def wants_fan_out(job: Job, params_override: dict[str, Any] | None) -> bool:
    if not job.for_each:
        return False
    raw = _raw(job, params_override)
    if isinstance(raw, (list, tuple)):
        return True
    return isinstance(raw, str) and (raw.strip().lower() in ALL or "," in raw)


async def fan_out_values(job: Job, params_override: dict[str, Any] | None, *, connectors: Any, explorer: Any = None) -> list[Any] | None:
    """The values to run the job for, or None when the fire is a plain single run. ``[]`` when
    "all" resolved to nothing (no choice) — the caller refuses the fire."""
    if not wants_fan_out(job, params_override):
        return None
    raw = _raw(job, params_override)
    items = [str(x).strip() for x in raw] if isinstance(raw, (list, tuple)) else [x.strip() for x in str(raw).split(",")]
    items = [x for x in items if x]
    if any(x.lower() in ALL for x in items):
        param = next((p for p in job.user_params if p.name == job.for_each), None)
        choices = await param_choices(param, connectors=connectors, explorer=explorer) if param else []
        items = [c["value"] for c in choices]
    seen: list[Any] = []
    for x in items:
        v = typed(x)
        if v not in seen:
            seen.append(v)
    return seen
