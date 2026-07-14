"""JobRegistry — owns the parsed ``jobs.toml`` and answers job lookups.

Mirrors :class:`~liberty.connectors.registry.ConnectorRegistry` in shape: a
parsed Pydantic config goes in, the registry exposes accessor methods, and
:func:`load_jobs` is the factory that wires path → TOML → ``${VAR}`` substitution
→ ``step_defaults`` merge → Pydantic validation. Hot-reload (Phase 13c) will
construct a fresh registry from a fresh file and swap it into ``app.state``.
"""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

from liberty.config import substitute_env
from liberty.jobs.schema import Job, JobsFile


class UnknownJobError(LookupError):
    """Raised by :meth:`JobRegistry.get` for an unknown job id."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"unknown job id: {job_id!r}")
        self.job_id = job_id


class JobRegistry:
    """Holds the parsed :class:`JobsFile` and lets callers look up jobs by id."""

    def __init__(self, config: JobsFile) -> None:
        self._config = config
        self._jobs: dict[str, Job] = {j.id: j for j in config.jobs}

    @property
    def config(self) -> JobsFile:
        return self._config

    def jobs(self) -> list[Job]:
        """All jobs, in file order."""
        return list(self._jobs.values())

    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise UnknownJobError(job_id) from None

    def scheduled_jobs(self) -> list[Job]:
        """The jobs the scheduler should register at startup: enabled AND carrying at
        least one cron trigger — either the job's own ``schedule`` OR a schedulable
        preset (a preset with its own ``schedule``, fired with that preset's params).

        ``enabled = false`` (scheduler-level) disqualifies a job entirely; a job with
        neither a job-level schedule nor any scheduled preset is manual-only (still
        triggerable via :samp:`POST /admin/jobs/{id}/run`).
        """
        return [
            j for j in self._jobs.values()
            if j.enabled and (j.schedule or any(p.schedule for p in j.presets))
        ]


# --------------------------------------------------------------------------- #
# loader
# --------------------------------------------------------------------------- #


def load_jobs(
    path: Path | str,
    *,
    env: dict[str, str] | None = None,
) -> JobRegistry:
    """Read ``jobs.toml`` at *path* and build a :class:`JobRegistry`.

    Order of operations:

    1. Read the file (a missing file → empty registry; no error).
    2. ``${NAME}`` / ``${NAME:-default}`` substitution against ``env`` (or the
       process environment when *env* is None).
    3. For each job, pop ``step_defaults`` and merge it into every step (the
       step's own values win on conflict; nested ``source``/``target``/``mapping``
       dicts are shallow-merged so step ``source.table`` doesn't wipe out
       ``source.connector`` from the defaults).
    4. Pydantic validation of the resulting :class:`JobsFile`.

    Returns an empty registry for a missing file — the framework should boot
    without nomaflow as long as the operator hasn't provided a catalogue yet.
    """
    path = Path(path)
    if not path.exists():
        return JobRegistry(JobsFile())
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    data = substitute_env(data, env=env)
    for job in data.get("jobs", []):
        defaults = job.pop("step_defaults", None)
        if defaults:
            job["steps"] = [
                _merge_step_defaults(defaults, step) for step in job.get("steps", [])
            ]
    return JobRegistry(JobsFile.model_validate(data))


_MERGEABLE_DICTS = ("source", "target", "headers", "mapping", "op_kwargs")


def _merge_step_defaults(defaults: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """One-level deep-merge of *defaults* into *step* (step's values win).

    The known nested-dict fields (``source``, ``target``, ``headers``,
    ``mapping``, ``op_kwargs``) get shallow-merged so a step that sets
    ``source.table`` keeps ``source.connector`` inherited from the defaults.
    Everything else is a straight overlay: if the step provides the key, it
    wins; otherwise it falls back to the default.
    """
    out: dict[str, Any] = deepcopy(defaults)
    for k, v in step.items():
        if k in _MERGEABLE_DICTS and isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out
