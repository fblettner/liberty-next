"""``/admin`` — nomaflow jobs.toml (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import HTTPException, Request, status
from liberty.web.admin._base import _drop_none, _snapshot_config, router
from pydantic import BaseModel, ValidationError

from liberty.web.jobs import Editor as JobsEditor

# ── nomaflow jobs.toml (Phase 13 — see docs/NOMAFLOW-UI.md §5) ────────────────────────────────
# Unlike pools/connectors/charts/dashboards (dict-keyed sections), jobs.toml's top level is a
# ``[[jobs]]`` *array of tables*. So the wire shape is a list, not a {key: value} map: GET
# returns ``{path, jobs: [...]}``, PUT accepts ``{jobs: [...]}``. The GET returns *merged* jobs
# (``[jobs.step_defaults]`` expanded into each step) — the editor works on fully-expanded steps;
# see NOMAFLOW-UI.md §4.


# The jobs editor is open to ``nomaflow:edit`` (see liberty/web/jobs.py), not only a superuser.


@router.get("/config/jobs/parsed", summary="Get jobs config")
async def get_jobs_parsed(request: Request, _: JobsEditor) -> dict[str, Any]:
    """The current ``jobs.toml`` parsed and normalised — ``{path, meta, jobs: [<Job dict>...]}``.

    A missing file → an empty job list (+ default meta). ``step_defaults`` is merged into
    each step (the loader does this); default-valued keys are dropped so the wire payload
    stays terse. ``by_alias=True`` so the ``schema`` field on SQL endpoints round-trips as
    ``schema`` (the model's Python attribute is ``schema_`` — see
    :class:`liberty.jobs.schema.SqlEndpoint`).

    ``meta`` carries the file-level settings — today the version + the retention policy.
    Returned with defaults included (``exclude_defaults=False``) so the UI sees the
    EFFECTIVE policy (30 / 100 / 60) rather than ``{}`` for a meta block that's still
    using the schema defaults — without this, the RetentionPanel can't initialise its
    inputs to the values the scheduler is actually using."""
    from liberty.jobs import load_jobs

    path = Path(request.app.state.settings.jobs.config_path)
    registry = load_jobs(path)
    return {
        "path": str(path),
        # Defaults included on purpose — see docstring. Without this the panel inputs
        # would be blank for a fresh install where the operator hasn't customised meta.
        "meta": registry.config.meta.model_dump(),
        "jobs": [
            j.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
            for j in registry.jobs()
        ],
    }


class JobsBody(BaseModel):
    jobs: list[dict[str, Any]]
    # Optional so existing callers that only PUT ``jobs`` (the JobEditor save flow)
    # keep working unchanged — when omitted, the file's existing [meta] block is
    # preserved. The RetentionPanel always sends meta so its edits land.
    meta: dict[str, Any] | None = None




def _restore_env_refs(new: Any, raw: Any) -> Any:
    """*new* (a job as the builder sends it — loaded with ``${…}`` resolved) with each value that
    equals the resolution of the file's ``${…}`` reference at the same place put back as that
    reference, so a save does not write one server's paths into a shipped file. Lists of named
    entries (steps, params) pair by ``name``, other lists by position."""
    from liberty.config import substitute_env

    if isinstance(raw, str) and isinstance(new, str):
        return raw if "${" in raw and substitute_env(raw) == new else new
    if isinstance(new, dict) and isinstance(raw, dict):
        return {k: _restore_env_refs(v, raw.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(raw, list):
        named = all(isinstance(x, dict) and "name" in x for x in raw)
        by_name = {x["name"]: x for x in raw} if named else {}
        return [_restore_env_refs(v, by_name.get(v.get("name")) if named and isinstance(v, dict) else (raw[i] if i < len(raw) else None))
                for i, v in enumerate(new)]
    return new


def _merge_jobs(doc: Any, text: str, path: Path, submitted: list[dict[str, Any]]) -> Any:
    """The ``jobs`` array to write: a submitted job identical to what the file loads to keeps its
    table as it is on disk (text, comments, ``${…}`` references, ``step_defaults``); a changed or new
    job is written from the submission with its ``${…}`` references restored. Order, additions and
    deletions follow the submission."""
    from liberty.jobs import load_jobs
    from liberty.jobs.registry import _merge_step_defaults
    from liberty.jobs.schema import Job

    old_tables = {str(t.get("id")): t for t in (doc.get("jobs") or [])}
    raw_jobs: dict[str, dict[str, Any]] = {}
    for j in (tomllib.loads(text).get("jobs") or []) if text.strip() else []:
        j = dict(j)
        defaults = j.pop("step_defaults", None)
        if defaults:
            j["steps"] = [_merge_step_defaults(defaults, st) for st in j.get("steps", [])]
        raw_jobs[str(j.get("id"))] = j
    loaded = {j.id: j for j in load_jobs(path).jobs()} if path.exists() else {}
    out = tomlkit.aot()
    for job in submitted:
        jid = str(job.get("id"))
        if jid in old_tables and jid in loaded and Job.model_validate(_drop_none(job)) == loaded[jid]:
            out.append(old_tables[jid])
        else:
            out.append(_drop_none(_restore_env_refs(job, raw_jobs.get(jid))))
    return out


@router.put("/config/jobs/parsed", summary="Update jobs config")
async def put_jobs_parsed(body: JobsBody, request: Request, principal: JobsEditor) -> dict[str, object]:
    """Validate the submitted jobs (+ optional meta) against :class:`liberty.jobs.JobsFile`,
    then rewrite ``jobs.toml`` via ``tomlkit`` — preserving comments + formatting on
    untouched sections. Re-parses the result before writing. Does not reload — call
    ``POST /admin/reload`` afterwards to apply.

    The body carries already-expanded steps (no ``step_defaults``); ``Job``'s ``extra="forbid"``
    rejects a stray ``step_defaults`` key. Saving a job therefore normalises its hand-authored
    ``step_defaults`` block away (NOMAFLOW-UI.md §4); the jobs left unchanged keep their text as it
    is on disk, ``${…}`` references included (``_merge_jobs``).

    ``meta`` is optional: when present it REPLACES the file's existing ``[meta]`` block
    (so retention edits land); when absent the existing block is preserved. The JobEditor
    flow omits meta + the RetentionPanel sends it — neither steps on the other.

    ``nomaflow:edit`` saves the jobs; the ``[meta]`` policies (retention, missed schedule) stay
    with a superuser."""
    from liberty.jobs import JobsFile

    if body.meta is not None and not principal.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Superuser required for the jobs policies ([meta])")

    try:
        validate_payload: dict[str, Any] = {"jobs": body.jobs}
        if body.meta is not None:
            validate_payload["meta"] = body.meta
        JobsFile.model_validate(validate_payload)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid jobs: {exc}") from exc

    path = Path(request.app.state.settings.jobs.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
    if body.jobs:
        # Only the jobs that changed are rewritten (``_merge_jobs``): the builder sends every job
        # loaded with ``${…}`` resolved, and writing them all back put one server's paths in the file.
        # TOML has no null: a cleared field (``null``) is dropped — the model's default takes over.
        doc["jobs"] = _merge_jobs(doc, text, path, body.jobs)
    elif "jobs" in doc:
        del doc["jobs"]
    # Replace the [meta] block when one was submitted. Assigning a plain dict lets
    # tomlkit lay it out as nested tables (``[meta]`` + ``[meta.retention]``) the
    # same way a hand-authored file would; the round-trip parse below catches any
    # malformed shape before it touches disk.
    if body.meta is not None:
        doc["meta"] = _drop_none(body.meta)   # a policy field left blank (files_days) arrives as null

    new_text = tomlkit.dumps(doc)
    try:
        # Validate the FULL re-parsed document, not just the jobs list — a meta-only
        # change wouldn't be caught by the jobs-only validate above.
        JobsFile.model_validate(tomllib.loads(new_text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"resulting jobs are invalid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_config(request, path)
    path.write_text(new_text, encoding="utf-8")
    return {"saved": True, "path": str(path)}
