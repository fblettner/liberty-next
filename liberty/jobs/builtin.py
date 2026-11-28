"""The nomaflow jobs the framework ships itself — in the list like the jobs of ``jobs.toml``
(visible, run now, paused, rescheduled), without having to be written there.

A built-in job is added to what :func:`liberty.jobs.registry.load_jobs` reads when no job of
``jobs.toml`` carries its id. The first change made to it — a pause, a new schedule in the job
editor — writes it into ``jobs.toml``, and from then on the file's version is the job: a
built-in can be paused or changed, not removed (deleted from the file, it is back as shipped).

Today: ``liberty-usage-report``, the daily licence usage report (:mod:`liberty.licensing.usage`)
— only on an installation that has a licence key and an address to report to.
"""

from __future__ import annotations

from typing import Any

from liberty.jobs.schema import Job

USAGE_JOB_ID = "liberty-usage-report"


def builtin_jobs(settings: Any) -> list[Job]:
    """The built-in jobs that apply to this installation."""
    jobs: list[Job] = []
    license_ = getattr(settings, "license", None)
    if license_ is not None and (license_.key or "").strip() and (license_.usage_url or "").strip():
        jobs.append(Job.model_validate({
            "id": USAGE_JOB_ID,
            "label": "Licence usage report", "l": {"fr": "Rapport d'utilisation de la licence"}, "category": "maintenance",
            "description": (
                "Licence usage report — once a day, tells the licence management tool which environment this is, "
                "its version and its number of enabled accounts. Nothing else is sent; each run shows what was. "
                "Pause it here, or blank [license] usage_url, to send nothing."
            ),
            "schedules": [{"name": "Every night", "schedule": "17 4 * * *"}],
            "tags": ["license", "maintenance"],
            "timeout_seconds": 120,
            "steps": [{"type": "python", "name": "send", "callable": "liberty.licensing.usage:send_usage_report"}],
        }))
    return jobs


def builtin_ids(settings: Any) -> set[str]:
    return {j.id for j in builtin_jobs(settings)}
