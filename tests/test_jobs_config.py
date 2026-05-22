"""Tests for the nomaflow jobs.toml parser (Phase 13a foundation).

Exercises :func:`liberty.jobs.load_jobs` and :class:`liberty.jobs.JobsFile` /
:class:`liberty.jobs.Job` / :class:`liberty.jobs.Step` — env-var substitution,
step_defaults merging, per-type required-field validation, and the registry
accessors. No scheduler, no runner, no DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from liberty.jobs import (
    BackoffKind,
    CopyMode,
    DecimalMode,
    JobRegistry,
    JobsFile,
    StepType,
    UnknownJobError,
    load_jobs,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# load_jobs — file plumbing
# --------------------------------------------------------------------------- #


def test_load_jobs_missing_file_returns_empty_registry(tmp_path: Path) -> None:
    """A missing jobs.toml is fine — the framework should boot before nomaflow
    has any jobs configured."""
    registry = load_jobs(tmp_path / "absent.toml")
    assert isinstance(registry, JobRegistry)
    assert registry.jobs() == []
    assert registry.scheduled_jobs() == []


def test_load_jobs_empty_file_returns_empty_registry(tmp_path: Path) -> None:
    """An empty TOML is also fine — operators may stub the file before adding jobs."""
    path = _write(tmp_path / "jobs.toml", "")
    registry = load_jobs(path)
    assert registry.jobs() == []


def test_load_jobs_minimal_sql_query_job(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "ping"
schedule = "*/5 * * * *"

[[jobs.steps]]
type = "sql_query"
name = "select 1"
connector = "default"
query = "select_one"
""")
    registry = load_jobs(path)
    jobs = registry.jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "ping"
    assert job.schedule == "*/5 * * * *"
    assert job.enabled is True
    assert len(job.steps) == 1
    step = job.steps[0]
    assert step.type is StepType.SQL_QUERY
    assert step.connector == "default"


# --------------------------------------------------------------------------- #
# ${VAR} substitution
# --------------------------------------------------------------------------- #


def test_env_var_substitution_in_callable_and_op_kwargs(tmp_path: Path) -> None:
    """${VAR} works anywhere — both in scalar fields and inside inline tables."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "envtest"

[[jobs.steps]]
type = "python"
name = "go"
callable = "${NF_CALLABLE}"
op_kwargs = { upstream = "${NF_UPSTREAM:-fallback-host}" }
""")
    registry = load_jobs(path, env={"NF_CALLABLE": "my.mod:fn"})
    step = registry.get("envtest").steps[0]
    assert step.callable == "my.mod:fn"
    # NF_UPSTREAM unset → falls back to the default
    assert step.op_kwargs == {"upstream": "fallback-host"}


def test_env_var_unset_with_no_default_becomes_empty_string(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "envtest2"

[[jobs.steps]]
type = "http"
name = "fetch"
url = "https://${NF_HOST}/api/ping"
""")
    registry = load_jobs(path, env={})
    step = registry.get("envtest2").steps[0]
    assert step.url == "https:///api/ping"  # NF_HOST → ""


# --------------------------------------------------------------------------- #
# step_defaults merging
# --------------------------------------------------------------------------- #


def test_step_defaults_merge_into_each_step(tmp_path: Path) -> None:
    """Reproduces the worked example from PHASE13.md §3.4 — step_defaults
    provides connector/mode/coercion; each step supplies its own schema/table."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "nomajde-daily-sync"
schedule = "30 2 * * *"

[jobs.step_defaults]
type = "sql_copy"
mode = "overwrite"
type_coercion = "jde"
source = { connector = "jde" }
target = { connector = "nomajde" }

[[jobs.steps]]
name = "F0004"
source = { schema = "PS920CTL", table = "F0004" }
target = { schema = "nomajde",  table = "f0004" }

[[jobs.steps]]
name = "F9200"
source = { schema = "DD920", table = "F9200" }
target = { schema = "nomajde", table = "f9200" }
""")
    registry = load_jobs(path)
    job = registry.get("nomajde-daily-sync")
    assert len(job.steps) == 2
    for step, expected_table in zip(job.steps, ["F0004", "F9200"]):
        # type / mode / coercion came from step_defaults
        assert step.type is StepType.SQL_COPY
        assert step.mode is CopyMode.OVERWRITE
        assert step.type_coercion == "jde"
        # source.connector came from defaults; source.schema/table came from the step
        assert step.source is not None and step.target is not None
        assert step.source.connector == "jde"
        assert step.source.table == expected_table
        assert step.target.connector == "nomajde"
        # the merged source dict didn't lose the inherited connector
        assert step.target.table == expected_table.lower()


def test_step_value_overrides_default(tmp_path: Path) -> None:
    """When both step_defaults and a step provide the same field, the step wins."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "override"

[jobs.step_defaults]
type = "sql_query"
connector = "default-conn"
query = "default-query"
timeout_seconds = 100

[[jobs.steps]]
name = "uses-defaults"

[[jobs.steps]]
name = "overrides"
connector = "other-conn"
timeout_seconds = 999
""")
    registry = load_jobs(path)
    s1, s2 = registry.get("override").steps
    assert s1.connector == "default-conn"
    assert s1.timeout_seconds == 100
    assert s2.connector == "other-conn"
    assert s2.timeout_seconds == 999
    # query falls back to default even on the overriding step
    assert s2.query == "default-query"


# --------------------------------------------------------------------------- #
# per-type required-field validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("step_toml, missing_field", [
    # sql_copy needs source + target
    ("""type = "sql_copy"
name = "no-endpoints" """, "source"),
    # sql_query needs connector + query
    ("""type = "sql_query"
name = "no-conn" """, "connector"),
    # python needs callable
    ("""type = "python"
name = "no-callable" """, "callable"),
    # ldap_sync needs the core LDAP fields
    ("""type = "ldap_sync"
name = "no-server" """, "server"),
    # http needs url
    ("""type = "http"
name = "no-url" """, "url"),
])
def test_step_required_fields(tmp_path: Path, step_toml: str, missing_field: str) -> None:
    path = _write(tmp_path / "jobs.toml", f"""
[[jobs]]
id = "missing-test"

[[jobs.steps]]
{step_toml}
""")
    with pytest.raises(ValidationError) as exc:
        load_jobs(path)
    # Make sure the error names the offending field so an operator can fix it.
    assert missing_field in str(exc.value)


def test_invalid_callable_shape_rejected(tmp_path: Path) -> None:
    """The ``module:function`` format is enforced at parse time; importability isn't."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "bad-callable"

[[jobs.steps]]
type = "python"
name = "go"
callable = "not_a_dotted_path"
""")
    with pytest.raises(ValidationError) as exc:
        load_jobs(path)
    assert "module" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# enums + timezone + id validation
# --------------------------------------------------------------------------- #


def test_decimal_mode_and_backoff_enums_round_trip(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "policy"

[jobs.retry]
attempts = 3
backoff = "exponential"
base_seconds = 60

[[jobs.steps]]
type = "sql_copy"
name = "preserve"
decimal_mode = "preserve"
source = { connector = "a", schema = "s", table = "t" }
target = { connector = "b", schema = "s", table = "t" }
""")
    registry = load_jobs(path)
    job = registry.get("policy")
    assert job.retry is not None
    assert job.retry.backoff is BackoffKind.EXPONENTIAL
    assert job.steps[0].decimal_mode is DecimalMode.PRESERVE


def test_unknown_step_type_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "bad-type"

[[jobs.steps]]
type = "spark_submit"
name = "nope"
""")
    with pytest.raises(ValidationError):
        load_jobs(path)


def test_unknown_timezone_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "tz-test"
timezone = "Atlantis/Lost-City"

[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    with pytest.raises(ValidationError) as exc:
        load_jobs(path)
    assert "timezone" in str(exc.value).lower() or "atlantis" in str(exc.value).lower()


def test_job_id_must_be_url_safe(tmp_path: Path) -> None:
    """Job ids end up in URLs (/admin/jobs/<id>/run) — restrict the character set."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "bad id with spaces!"

[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    with pytest.raises(ValidationError):
        load_jobs(path)


def test_duplicate_job_ids_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "dup"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "dup"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q2"
""")
    with pytest.raises(ValidationError) as exc:
        load_jobs(path)
    assert "duplicate" in str(exc.value).lower()


def test_extra_fields_at_job_or_step_level_rejected(tmp_path: Path) -> None:
    """``extra="forbid"`` catches typos before they become silent no-ops at runtime."""
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "typo"
schedule_typo = "*/5 * * * *"

[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    with pytest.raises(ValidationError):
        load_jobs(path)


# --------------------------------------------------------------------------- #
# JobRegistry accessors
# --------------------------------------------------------------------------- #


def test_scheduled_jobs_filters_disabled_and_manual_only(tmp_path: Path) -> None:
    path = _write(tmp_path / "jobs.toml", """
[[jobs]]
id = "cron"
schedule = "*/5 * * * *"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "manual-only"
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"

[[jobs]]
id = "disabled"
schedule = "0 0 * * *"
enabled = false
[[jobs.steps]]
type = "sql_query"
name = "s"
connector = "c"
query = "q"
""")
    registry = load_jobs(path)
    assert {j.id for j in registry.jobs()} == {"cron", "manual-only", "disabled"}
    assert [j.id for j in registry.scheduled_jobs()] == ["cron"]


def test_unknown_job_lookup_raises(tmp_path: Path) -> None:
    registry = load_jobs(tmp_path / "absent.toml")
    with pytest.raises(UnknownJobError) as exc:
        registry.get("nope")
    assert "nope" in str(exc.value)
