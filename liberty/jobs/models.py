"""SQLAlchemy ORM models for nomaflow's run history — see ``docs/PHASE13.md`` §7.

Two tables: ``nomaflow_job_runs`` (one row per fire — scheduled or manual) and
``nomaflow_step_runs`` (one row per step attempt, FK to the parent run). The
unique ``(job_id, scheduled_at)`` constraint dedups duplicate cron fires; NULL
``scheduled_at`` (manual triggers) is allowed multiple times — Postgres treats
NULLs as distinct in unique indexes.

These live on whichever pool ``[jobs] pool`` names (default ``"default"``, same
as auth). The scope-to-its-own-Base trick (a private :class:`DeclarativeBase`)
keeps ``create_all`` from touching the auth tables and vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for the nomaflow tables only — keeps
    ``Base.metadata.create_all`` scoped to runs/steps and nothing else."""


class RunState(str, Enum):
    """Lifecycle states for both ``JobRun`` and ``StepRun`` — see PHASE13.md §4."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class TriggerKind(str, Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


# Stored as plain strings in the DB so the values stay greppable and a
# future state addition doesn't need an ALTER TYPE on Postgres enums.
_STATE_COL = String(16)


class JobRun(Base):
    __tablename__ = "nomaflow_job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(128))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(_STATE_COL, nullable=False, default=RunState.QUEUED.value)
    error_message: Mapped[str | None] = mapped_column(String(4096))
    rows_affected: Mapped[int | None] = mapped_column(BigInteger)

    step_runs: Mapped[list["StepRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin",
        order_by="StepRun.step_index, StepRun.attempt",
    )

    __table_args__ = (
        # Dedup scheduled fires for the same (job, scheduled_at). Manual triggers
        # have scheduled_at = NULL, which Postgres allows arbitrarily many of.
        UniqueConstraint("job_id", "scheduled_at", name="uq_nomaflow_job_runs_job_sched"),
        Index("ix_nomaflow_job_runs_job_started", "job_id", "started_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"JobRun(id={self.id!r}, job_id={self.job_id!r}, state={self.state!r})"


class StepRun(Base):
    __tablename__ = "nomaflow_step_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nomaflow_job_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(256), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(_STATE_COL, nullable=False, default=RunState.QUEUED.value)
    error_message: Mapped[str | None] = mapped_column(String(4096))
    rows_affected: Mapped[int | None] = mapped_column(BigInteger)
    # The runner truncates to ~4 KB before INSERT — full logs go through the file logger.
    log_excerpt: Mapped[str | None] = mapped_column(String(4096))

    run: Mapped[JobRun] = relationship(back_populates="step_runs")

    __table_args__ = (
        Index("ix_nomaflow_step_runs_run", "run_id", "step_index"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"StepRun(id={self.id!r}, run_id={self.run_id!r}, "
            f"step={self.step_index}:{self.step_name!r}, state={self.state!r})"
        )


class RunLog(Base):
    """The captured log text for one run — see :mod:`liberty.jobs.runlog`.

    One row per run, written once at run finalize (the in-memory buffer is the
    live source while a run is active; this is the durable copy for after).
    A separate table, not a column on ``nomaflow_job_runs``, so ``create_all``
    adds it on the next boot with no ALTER — nomaflow has no Alembic yet.
    """

    __tablename__ = "nomaflow_run_logs"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nomaflow_job_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    logs: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RunLog(run_id={self.run_id!r}, chars={len(self.logs)})"


class StepRunExtras(Base):
    """Free-form structured metadata a step's executor wanted to attach to its
    outcome — see :attr:`liberty.jobs.steps.base.StepResult.extras`.

    Today the only writer is :class:`liberty.jobs.steps.CallJobExecutor` (stores
    ``{child_run_id, child_job_id}`` so the Run Detail page can render the
    step row as a link to the spawned child run); future step types can attach
    anything JSON-serialisable here without touching the schema. Same separate-
    table reasoning as :class:`RunLog` / :class:`RunOverrides`: a new column
    on ``nomaflow_step_runs`` would need an ALTER on every existing deployment,
    and nomaflow has no Alembic yet — a new table adds itself via ``create_all``
    with no migration step. Rows without extras don't get an entry at all.
    """

    __tablename__ = "nomaflow_step_run_extras"

    step_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nomaflow_step_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    extras_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"StepRunExtras(step_run_id={self.step_run_id!r})"


class RunOverrides(Base):
    """The per-fire overrides captured when a run was started — what the
    operator picked in the Run-with-parameters modal (or the empty defaults
    for a scheduled fire). Surfaced on the Run Detail page so operators can
    answer "why did THIS run produce different row counts than the scheduled
    one?" without digging through the log.

    Same separate-table reasoning as :class:`RunLog`: a new column on
    ``nomaflow_job_runs`` would need an ALTER on every existing deployment,
    and nomaflow has no Alembic yet — a new table adds itself via
    ``create_all`` with no migration step. Rows missing from this table
    (existing runs from before this feature landed) just render as "no
    overrides" in the UI, the same shape a scheduled fire produces.

    The JSON shape mirrors the request body of POST /admin/jobs/<id>/run:
    ``{"log_level": str | None, "params": dict | None, "op_kwargs":
    {step_name: {k: v}}, "step_enabled": {step_name: bool}}``. Empty / None
    keys are omitted so a no-override run stores ``{}`` rather than four
    explicit nulls.
    """

    __tablename__ = "nomaflow_run_overrides"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nomaflow_job_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # JSON-serialised on write, parsed by the route layer on read. A TEXT
    # column rather than the Postgres-native JSONB so the SQLite test fixture
    # works without dialect-specific handling.
    overrides_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RunOverrides(run_id={self.run_id!r}, len={len(self.overrides_json)})"
