"""Pydantic schema for ``jobs.toml`` — see the nomaflow operating guide (apps repo ``docs/NOMAFLOW.md``)

The file's top level is a list of ``[[jobs]]`` blocks plus an optional
``[meta]`` section. Each job carries an optional retry/alerts policy, an
optional ``step_defaults`` shape that's merged into every step at *load time*
(not here — see :func:`liberty.jobs.registry.load_jobs`), and a non-empty
``steps`` list.

Validation here is structural: required fields, the closed sets of enums
(step type, copy mode, decimal mode, backoff kind), and IANA timezone names.
Two intentional non-validations: cron expressions (stored as strings; the
scheduler library will reject malformed ones at register time, with a better
message than we'd produce) and Python ``callable`` strings (the loader can't
prove the module is importable; the runner will fail loudly on resolution
when the step actually fires).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# enums — the closed sets
# --------------------------------------------------------------------------- #


class StepType(str, Enum):
    SQL_COPY = "sql_copy"
    SQL_QUERY = "sql_query"
    PYTHON = "python"
    LDAP_SYNC = "ldap_sync"
    HTTP = "http"
    # call_job — fires another job inline within the calling step (waits for the
    # child to terminate before continuing). The child gets its own JobRun row
    # so the run history stays uniform; cycle detection is in-memory via the
    # parent chain on RunContext (see :class:`CallJobExecutor`). Inherits the
    # parent's log_level (so DEBUG traces the whole chain) and any per-fire
    # params_override (so an operator's "run with apps_id=42" cascades).
    CALL_JOB = "call_job"
    FILE_LOAD = "file_load"
    # C5 — the digest: render a saved explorer analysis / dashboard or a plugin report to a
    # file in the run's work area, then mail it (``notify`` attaches the run's files).
    REPORT = "report"
    NOTIFY = "notify"


class CopyMode(str, Enum):
    OVERWRITE = "overwrite"
    APPEND = "append"
    UPSERT = "upsert"  # spec §11 #5 may drop this in 13a; kept for now to validate intent


class LoadMode(str, Enum):
    """``file_load``: just insert, or delete the partitions the file carries first (one transaction)."""
    APPEND = "append"
    REPLACE_PARTITION = "replace_partition"


class DecimalMode(str, Enum):
    TRUNCATE = "truncate"
    PRESERVE = "preserve"


class BackoffKind(str, Enum):
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


# --------------------------------------------------------------------------- #
# policies (per-job)
# --------------------------------------------------------------------------- #


class JobRetry(BaseModel):
    """Applies to each step independently; the runner consults this on step failure."""

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(1, ge=1)
    backoff: BackoffKind = BackoffKind.FIXED
    base_seconds: int = Field(60, ge=0)


class JobAlerts(BaseModel):
    """Fan-out via the Phase 9 Socket.IO ``/technical`` room (and any recipients)."""

    model_config = ConfigDict(extra="forbid")

    on_failure: bool = True
    on_long_run_minutes: int | None = Field(None, ge=1)
    recipients: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# step endpoints (sql_copy needs source + target; other types ignore these)
# --------------------------------------------------------------------------- #


class SqlEndpoint(BaseModel):
    """A connector + (schema, table) target for the SQL step types.

    All fields are optional here because they may come from the job's
    ``step_defaults`` — the merger fills them before the step is validated for
    completeness in :meth:`Step._check_required_fields`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connector: str | None = None
    # Optional pool override — run this endpoint's connector against a specific pool (a
    # different DB/JDE instance) instead of the connector's default. The pool must be in the
    # connector's allowed set (``[pool] + pools``). None ⇒ the connector's default pool. Lets
    # one sql_copy job target several environments by only swapping source/target pools.
    pool: str | None = None
    # ``schema`` is a reserved word on Pydantic BaseModels (model_schema, etc.);
    # alias lets TOML/JSON use the natural ``schema`` key while Python sees ``schema_``.
    schema_: str | None = Field(None, alias="schema")
    table: str | None = None


# --------------------------------------------------------------------------- #
# step (one model, conditional validation by type)
# --------------------------------------------------------------------------- #


class Step(BaseModel):
    """One unit of work in a job's pipeline.

    A single model for all step types — easier to merge ``step_defaults`` into,
    easier to evolve. The runner discriminates on ``type`` and reads the fields
    it needs; :meth:`_check_required_fields` enforces the per-type minimums at
    parse time.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: StepType
    name: str
    # Per-step disable switch. ``False`` makes the runner skip the step (recorded
    # as CANCELED with "skipped: disabled" in the StepRun row, so the UI still
    # shows it in the timeline instead of leaving a gap that would confuse the
    # operator). Defaults to ``True`` (every step runs). Operators set this in
    # jobs.toml for steps that are conditionally relevant (e.g. an OUT_PURGE
    # step that should only run weekly while the rest runs daily — disable in
    # the per-step entry, the scheduler still walks the rest), OR per-fire via
    # the Run-with-parameters modal (``step_enabled`` override) when only some
    # phases need re-running after an upstream failure.
    enabled: bool = True

    # sql_copy / sql_query — shared SQL endpoints
    source: SqlEndpoint | None = None
    target: SqlEndpoint | None = None
    connector: str | None = None
    query: str | None = None
    # sql_query: optional pool override for ``connector`` (run against a different DB/JDE
    # instance in the connector's allowed set). None ⇒ the connector's default pool.
    pool: str | None = None
    # sql_query: bound parameters passed through to SQLConnector.execute(params=...)
    params: dict[str, Any] = Field(default_factory=dict)

    # sql_copy specifics
    mode: CopyMode | None = None
    type_coercion: str | None = None  # "jde" | "none" — free string today; enum if it grows
    decimal_mode: DecimalMode | None = None
    batch_size: int = Field(10_000, ge=1)
    # Per-table list of column names whose values get full ``strip()`` (both ends) instead of
    # the default rstrip when the source pool's ``trim_strings`` is on. Use for JDE-style
    # right-justified codes left-padded with spaces — F0005's ``DRKY`` / ``DRMCU`` / the like.
    # **Per-table on purpose**: JDE column names embed a 2-letter table prefix, so the same
    # data-dictionary item (``KY``) appears as ``DRKY`` in F0005 and ``ABKY`` in F0101 — a
    # pool-wide list would have the wrong granularity. Matched case-insensitively against
    # the introspected source column name; ignored when ``trim_strings`` is off.
    strip_both_columns: list[str] = Field(default_factory=list)

    # python
    callable: str | None = None  # ``module:function`` — see spec §5.3
    op_kwargs: dict[str, Any] = Field(default_factory=dict)

    # ldap_sync — paged search on the bind/base/filter, every entry pushed through
    # ``target_query`` on ``target_connector`` with params built from ``mapping``
    # (LDAP attr → param name). See :class:`LdapSyncExecutor`.
    server: str | None = None
    bind_dn: str | None = None
    bind_password: str | None = None
    search_base: str | None = None
    search_filter: str | None = None
    attributes: list[str] = Field(default_factory=list)
    target_connector: str | None = None
    target_query: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)

    # http — single request, success on 2xx. ``body`` accepts None / str / bytes /
    # dict / list (dict and list are JSON-encoded; the executor seeds
    # ``Content-Type: application/json`` when no header was set). See
    # :class:`HttpStepExecutor`.
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None

    # call_job — fires another job (by id) inline within this step. The child
    # runs to terminal-state before this step returns; the child's outcome
    # decides this step's result (FAILED→fail, CANCELED→cancel, SUCCEEDED→ok
    # with the child's total rows_affected). Cycle detection is in-memory via
    # ``RunContext.parent_chain`` — calling a job already in the chain fails
    # the step at fire time with "cycle detected: A → B → A".
    target_job_id: str | None = None

    # file_load — a spreadsheet / CSV into ``target`` (see :class:`FileLoadExecutor`).
    # ``path`` / ``constants`` values take ``${params.x}`` (the run parameters), ``${run.id}``,
    # ``${job.id}`` — dotted on purpose, a plain ``${NAME}`` is env-substituted at load time. ``columns`` maps target column → file header (or ``#<position>``); the rest
    # match by name. ``partition_by`` names the target columns a ``replace_partition`` load
    # deletes by (their values from ``constants`` or the file).
    path: str | None = None
    watch_dir: str | None = None
    watch_pattern: str = "*"
    sheet: str | int | None = None
    header_row: int = Field(1, ge=1)
    delimiter: str | None = None
    encoding: str = "utf-8-sig"
    columns: dict[str, str] = Field(default_factory=dict)
    constants: dict[str, Any] = Field(default_factory=dict)
    load_mode: LoadMode | None = None
    partition_by: list[str] = Field(default_factory=list)
    on_error: Literal["fail", "skip"] = "fail"
    # Guards. ``refuse_query`` (a named query on ``refuse_connector``, default the target's) runs
    # first with the run parameters + constants as binds — and, with ``partition_by``, once per
    # partition the file carries with the partition columns as binds: any row back refuses the
    # load, the row's first column being the reason ("period 2026-03 is closed"). ``control_column`` is summed over
    # the inserted rows and compared with ``control_total`` — a value, ``${params.x}``, or ``@B2``
    # (a cell of the file) — within ``control_tolerance``; a mismatch rolls the load back.
    refuse_query: str | None = None
    refuse_connector: str | None = None
    control_column: str | None = None
    control_total: str | None = None
    control_tolerance: float = Field(0.0, ge=0)

    # report — ``analysis:<id>`` / ``dashboard:<id>`` (a saved explorer object of ``app`` /
    # ``model``) or ``report:<scope>:<id>`` (a plugin report, with ``params``); rendered to
    # ``format`` under ``as_user``'s or ``as_role``'s row-level visibility, over ``period`` (a
    # preset such as ``lastMonth``) or ``window_from`` / ``window_to``; the file lands in the
    # run's work area as ``filename`` (default: the object's id).
    report: str | None = None
    app: str | None = None
    model: str | None = None
    format: Literal["xlsx", "pdf"] | None = None
    period: str | None = None
    window_from: str | None = None
    window_to: str | None = None
    as_user: str | None = None
    as_role: str | None = None
    filename: str | None = None
    # notify — the recipients (``user:`` / ``role:`` / ``email:``), the subject / body (with
    # ``{JOB}`` ``{RUN_ID}`` ``{FILES}`` placeholders), the run files to attach (``["*"]`` = every
    # file this run produced) and the channels.
    recipients: list[str] = Field(default_factory=list)
    subject: str | None = None
    attach: list[str] = Field(default_factory=lambda: ["*"])
    channels: list[Literal["portal", "email", "action"]] = Field(default_factory=lambda: ["portal", "email"])

    # universal
    timeout_seconds: int = Field(3600, ge=1)

    @field_validator("callable")
    @classmethod
    def _validate_callable(cls, v: str | None) -> str | None:
        # Only enforce the ``module:function`` shape; importability is a runtime concern.
        if v is None:
            return v
        if ":" not in v or v.startswith(":") or v.endswith(":"):
            raise ValueError(
                "python step `callable` must be 'module.path:function_name'"
            )
        return v

    @model_validator(mode="after")
    def _check_required_fields(self) -> "Step":
        if self.type is StepType.SQL_COPY:
            self._require("source", "target")
        elif self.type is StepType.SQL_QUERY:
            self._require("connector", "query")
        elif self.type is StepType.PYTHON:
            self._require("callable")
        elif self.type is StepType.LDAP_SYNC:
            self._require("server", "bind_dn", "search_base", "target_connector", "target_query")
        elif self.type is StepType.HTTP:
            self._require("url")
        elif self.type is StepType.CALL_JOB:
            self._require("target_job_id")
        elif self.type is StepType.REPORT:
            self._require("report")
            kind, _, rest = (self.report or "").partition(":")
            if kind not in ("analysis", "dashboard", "report") or not rest:
                raise ValueError(f"step {self.name!r}: `report` is analysis:<id>, dashboard:<id> or report:<scope>:<id>")
            if kind in ("analysis", "dashboard"):
                self._require("app", "model")
            elif ":" not in rest:
                raise ValueError(f"step {self.name!r}: a plugin report is report:<scope>:<id>")
            if (self.window_from is None) != (self.window_to is None):
                raise ValueError(f"step {self.name!r}: `window_from` and `window_to` go together")
        elif self.type is StepType.NOTIFY:
            self._require("recipients", "subject")
        elif self.type is StepType.FILE_LOAD:
            self._require("target")   # the file comes from `path`, `watch_dir`, or an upload at run time
            if (self.control_column is None) != (self.control_total is None):
                raise ValueError(f"step {self.name!r}: `control_column` and `control_total` go together")
            if self.load_mode is LoadMode.REPLACE_PARTITION and not self.partition_by:
                raise ValueError(f"step {self.name!r}: load_mode = replace_partition needs `partition_by`")
        return self

    def _require(self, *fields: str) -> None:
        missing = [f for f in fields if getattr(self, f) in (None, "", [], {})]
        if missing:
            raise ValueError(
                f"step {self.name!r} (type={self.type.value!r}) is missing required "
                f"field(s): {', '.join(missing)}"
            )


# --------------------------------------------------------------------------- #
# job + jobs file
# --------------------------------------------------------------------------- #


class JobPreset(BaseModel):
    """A named snapshot of the Run-with-parameters modal state for one job.

    Operators save common shapes ("DEBUG security users only", "OUT module
    only") as presets — the modal exposes them as a dropdown above the form
    so the next operator picking the same combo just clicks the preset
    instead of re-toggling six steps + flipping the log level.

    All four fields are optional — a preset that only sets ``log_level`` is
    fine. The modal applies them by layering on top of the job's saved
    defaults, same merge order as a hand-typed override:
      1. job's saved defaults
      2. preset values (when applied)
      3. operator's modal edits (after the preset is applied)
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Display label in the preset dropdown.")
    log_level: Literal["WARNING", "INFO", "DEBUG"] | None = Field(
        default=None,
        description="Log-level override. None → inherit the job's saved log_level.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Shared-param overrides (apply to every step's merge).",
    )
    op_kwargs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-step op_kwargs overrides — same shape as the modal's payload "
            "(``{step_name: {key: value}}``). Wins over both ``params`` and the "
            "step's saved op_kwargs."
        ),
    )
    step_enabled: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Per-step enable / disable toggle — only steps the operator "
            "explicitly flipped from the job's saved ``Step.enabled`` are "
            "captured. Empty means 'run every step at its saved default'."
        ),
    )
    schedule: str | None = Field(
        default=None,
        description=(
            "Optional cron expression. When set, the scheduler fires THIS preset "
            "on its own schedule, applying the preset's params / op_kwargs / "
            "log_level / step_enabled — so one job can run on several schedules "
            "with different parameters without cloning. None → manual-only preset."
        ),
    )
    timezone: str | None = Field(
        default=None,
        description="IANA timezone for ``schedule``. None → inherit the job's timezone (then system tz).",
    )
    enabled: bool = Field(
        default=True,
        description=(
            "Whether this preset's ``schedule`` fires. Independent of the job's own "
            "``enabled`` flag: an enabled preset fires on its cron even when the job is "
            "disabled (the job's ``enabled`` gates only the job's OWN schedule). Ignored "
            "for a preset with no ``schedule`` (manual-only presets are always pickable)."
        ),
    )

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


class UserParam(BaseModel):
    """A parameter a USER sets when running the job from its menu item (the ``job`` page) —
    the only per-run override that side has. The value lands in ``op_kwargs[step][name]``.
    ``choices`` lists fixed values; ``choices_from = "explorer_models:<app>"`` offers the app's
    Explorer models (value = the model id, label = its label). A blank value means "not set"
    (the callable's default, e.g. every model)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="The op_kwargs key the value is written to.")
    label: str | None = Field(default=None, description="What the page shows (default: the name).")
    l: dict[str, str] = Field(default_factory=dict, description="Per-language label overrides.")
    step: str = Field(..., min_length=1, description="The step receiving the value.")
    default: str | None = Field(default=None, description="Pre-selected value.")
    required: bool = False
    choices: list[str] = Field(default_factory=list, description="Fixed choices (a dropdown).")
    choices_from: str | None = Field(default=None, description="A dynamic choice list: ``explorer_models:<app>``.")

    def label_for(self, language: str | None) -> str:
        if language and self.l.get(language):
            return self.l[language]
        return self.label or self.name


class Job(BaseModel):
    """One scheduled (or manual-only) unit of work."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str | None = None
    schedule: str | None = None  # cron expression; None → manual-only
    timezone: str | None = None  # IANA name (validated); None → system tz
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    retry: JobRetry | None = None
    alerts: JobAlerts | None = None
    steps: list[Step] = Field(..., min_length=1)
    timeout_seconds: int = Field(14_400, ge=1)
    # Job-level kwargs merged UNDER every python step's ``op_kwargs`` (step wins on
    # conflict). The point: most agent jobs (nomasx1-security, nomasx1-database, …)
    # call N module-specific callables that all need the same ``apps_id`` /
    # ``source_connector`` / ``target_connector``. Setting those once at the job level
    # avoids duplicating them on every step + keeps "change the target for this run"
    # to one edit in the Run-with-parameters modal. Defaults to ``{}``; only python
    # steps consume these (sql_copy / sql_query ignore them — the merge still happens,
    # but those executors look at typed fields, not op_kwargs).
    params: dict[str, Any] = Field(default_factory=dict)
    # What a USER may set from the job's menu page (see UserParam) — nothing by default.
    user_params: list[UserParam] = Field(default_factory=list)
    # Per-run logging verbosity. INFO (default) gives operator-level signal —
    # row counts + business progress markers. DEBUG also emits the full SQL of
    # every run_query (useful when troubleshooting a specific job — matches v1's
    # per-job debug flag). WARNING is the quiet end — only warnings + errors
    # reach the run log (INFO progress lines suppressed). The Run-with-parameters
    # modal can override this per-fire without editing the TOML; the per-fire
    # value wins.
    log_level: Literal["WARNING", "INFO", "DEBUG"] = "INFO"
    # Named presets of the Run-with-parameters modal state — saved by operators
    # ("DEBUG security users only", "OUT module only", …) so the next operator
    # picking the same combo clicks once. Stored inline on the job so the
    # presets travel with jobs.toml (same load/save flow, no extra table).
    # Empty by default; the modal hides the dropdown when this list is empty
    # so jobs without presets stay visually clean.
    presets: list[JobPreset] = Field(default_factory=list)
    # ──────────────────────────────────────────────────────────────────────
    # Install-time ordering. When set, ``liberty-admin run-install-jobs`` fires
    # this job during the post-install phase (in ascending step order). Jobs
    # without ``install_step`` are ignored by the install runner — they still
    # cron-fire / manual-fire normally. Idempotent: the install runner skips a
    # job that already has a SUCCEEDED run (use --force to re-run).
    # Typical usage: deploy-databases (1) → init-db (2) → init-schema (3) →
    # import-reference (4). One-shot bootstrap of a fresh deployment.
    install_step: int | None = Field(default=None, ge=1)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        # Used in URLs (`/admin/jobs/<id>/run`) and as a DB key — keep it URL-safe.
        bad = [c for c in v if not (c.isalnum() or c in "-_")]
        if bad:
            raise ValueError(
                f"job id {v!r} contains invalid characters {''.join(sorted(set(bad)))!r}; "
                "use letters, digits, '-', '_'"
            )
        return v

    @model_validator(mode="after")
    def _unique_scheduled_preset_names(self) -> "Job":
        # The scheduler registers one APScheduler trigger per *scheduled* preset, keyed by
        # preset name — two scheduled presets sharing a name would collide (one silently
        # replaces the other). Names of manual-only presets are free to repeat (harmless).
        scheduled = [p.name for p in self.presets if p.schedule]
        dupes = sorted({n for n in scheduled if scheduled.count(n) > 1})
        if dupes:
            raise ValueError(
                f"job {self.id!r}: scheduled presets must have unique names; duplicated: {dupes}"
            )
        return self


class RetentionPolicy(BaseModel):
    """How long nomaflow keeps the per-run history (the ``nomaflow_job_runs`` table and its
    FK-cascading children: ``nomaflow_step_runs`` / ``nomaflow_run_logs`` /
    ``nomaflow_run_overrides`` / ``nomaflow_step_run_extras``).

    Without retention the run tables grow forever — a daily nomasx1-security job at 5
    steps + 50 KB of logs per run accumulates ~18 MB / year just from one job, and a busy
    deployment with a dozen high-frequency cron jobs blows past 1 GB in 18 months. The
    policy gives operators a knob without needing a separate cron + custom SQL.

    Two axes deliberately, because each one alone has a degenerate case:

    * ``days`` — primary "time-based" axis. Deletes finished runs (SUCCEEDED / FAILED /
      CANCELED) whose ``finished_at`` is older than ``now - days``. Catches the bulk of
      old data; reasonable default for compliance windows ("90 days of audit history").
    * ``keep_last_per_job`` — secondary "count-based" axis. Even within the time window,
      caps each job's history at N most-recent runs. Protects against a job that fires
      every 5 minutes burying the run list with 12 × 24 = 288 rows / day of mostly
      identical SUCCEEDED runs (the operator only ever looks at the last few).

    The two are applied as a union — a run is deleted if EITHER rule says so. Running
    rows (state in {QUEUED, RUNNING}) are NEVER touched by retention, regardless of
    age. The cascade FK on the child tables means a single DELETE on
    ``nomaflow_job_runs`` cleans up the whole tree atomically.

    ``sweep_interval_minutes`` controls how often the scheduler fires the cleanup;
    60 is a reasonable default (the math: a sweep that takes 1 second running hourly
    is 0.03% overhead vs. checking every 5 minutes which is 0.4%). ``enabled = false``
    disables the recurring sweep entirely — operators still get the manual ``POST
    /admin/jobs/retention/sweep`` button for one-off cleanups.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Master toggle. False → no recurring sweep (manual endpoint still works).",
    )
    days: int = Field(
        default=30, ge=1, le=3650,
        description="Delete finished runs whose finished_at is older than this many days.",
    )
    keep_last_per_job: int = Field(
        default=100, ge=1, le=100_000,
        description="Cap each job's history at this many most-recent finished runs (even within the day window).",
    )
    sweep_interval_minutes: int = Field(
        default=60, ge=5, le=1440,
        description="How often the recurring sweep fires (minutes). 60 is a reasonable default.",
    )


class MissedPolicy(BaseModel):
    """Missed-schedule detection: every ``check_interval_minutes`` the scheduler lists the cron
    instants each active trigger should have fired since the last check and looks for a run
    near each one. An instant with no run (the server was down, or APScheduler skipped the
    fire because the previous run was still running) is recorded as a ``MISSED`` run — visible
    in the run history, deduplicated by the ``(job_id, scheduled_at)`` constraint so a restart
    never alerts twice — and raised as the ``missed`` job event for the notification rules.

    ``grace_minutes`` is how late a fire may start before it counts as missed (a slow
    scheduler tick, a queued fire). ``lookback_hours`` bounds the first check after a start:
    the window opens at the job's last recorded run, but never earlier than this.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Master toggle for the recurring check.")
    grace_minutes: int = Field(default=10, ge=1, le=1440, description="A fire may start this late and still count.")
    check_interval_minutes: int = Field(default=5, ge=1, le=1440, description="How often the check runs.")
    lookback_hours: int = Field(default=24, ge=1, le=168, description="How far back the first check after a start may look.")


class _Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    missed: MissedPolicy = Field(default_factory=MissedPolicy)
    # Retention is per-deployment, not per-job — lives on [meta] so it travels with
    # jobs.toml (one source of truth for "everything about how this deployment runs
    # nomaflow"). Defaults to an enabled policy with 30 days / 100 runs / hourly
    # sweep so a fresh install starts cleaning up immediately without operator action.
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)


class JobsFile(BaseModel):
    """Top-level shape of ``jobs.toml`` after env substitution + step_defaults merge."""

    model_config = ConfigDict(extra="forbid")

    meta: _Meta = Field(default_factory=_Meta)
    jobs: list[Job] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_job_ids(self) -> "JobsFile":
        seen: set[str] = set()
        for j in self.jobs:
            if j.id in seen:
                raise ValueError(f"duplicate job id: {j.id!r}")
            seen.add(j.id)
        return self
