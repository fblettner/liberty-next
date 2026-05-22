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

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
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
