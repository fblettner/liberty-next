"""``sql_query`` executor — run a named query on a configured SQL connector.

The simplest step type. Mostly a thin wrapper around
:meth:`liberty.connectors.SQLConnector.execute`: look up the connector by name,
look up the named query inside it, invoke with the step's ``params``, surface
the result as a :class:`StepResult`.

What this executor *doesn't* do (deliberately): pass ``column_hints`` (screen
machinery), ``audit_table`` (Screen-side flag), ``screen_max_rows`` (per-screen
cap), or ``user`` (the connector defaults to ``"anonymous"`` and that's the
right identity for a job — the audit trail belongs in nomaflow's own
``nomaflow_job_runs.triggered_by``, not the per-statement AUD_<table>).
"""

from __future__ import annotations

import logging

from liberty.connectors import (
    ConnectorError,
    ConnectorRegistry,
    QueryNotFoundError,
    UnknownConnectorError,
)
from liberty.connectors.sql import SQLConnector
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)


class SqlQueryExecutor:
    """Executes one ``sql_query`` step.

    The constructor takes a :class:`ConnectorRegistry` — same object the route
    layer uses — so the executor sees connector reloads (Phase 13c) the
    same way the rest of the app does. Hot-reload swaps the registry on
    ``app.state``; existing executors keep their reference to the old registry
    until the runner is rebuilt too, which is fine for in-flight runs.
    """

    def __init__(self, connectors: ConnectorRegistry) -> None:
        self._connectors = connectors

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.SQL_QUERY:
            raise StepFailed(
                f"SqlQueryExecutor received a step of type {step.type.value!r} — "
                "the runner wired the wrong executor for this step type"
            )
        if step.connector is None or step.query is None:
            # Defensive — schema validation already enforces these, but a hand-
            # constructed Step from a test could slip through. Raise with the
            # field names so a debugger sees what went missing.
            raise StepFailed(
                f"sql_query step {step.name!r} is missing connector/query "
                f"(connector={step.connector!r}, query={step.query!r})"
            )

        try:
            connector = self._connectors.get(step.connector)
        except UnknownConnectorError as exc:
            # Map registry errors into the runner's failure type so retries +
            # state-machine reporting behave consistently regardless of where
            # the error originated.
            raise StepFailed(str(exc)) from exc

        if not isinstance(connector, SQLConnector):
            raise StepFailed(
                f"sql_query step {step.name!r} targets connector {step.connector!r} "
                f"which is not a SQL connector (got {type(connector).__name__})"
            )
        # Optional pool override — run against a different DB/JDE instance in the connector's
        # allowed set (ConnectorError → StepFailed if the pool isn't allowed).
        try:
            connector = connector.for_pool(step.pool)
        except ConnectorError as exc:
            raise StepFailed(str(exc)) from exc

        _log.info(
            "nomaflow.sql_query run=%s step=%r connector=%s pool=%s query=%s",
            ctx.run_id, step.name, step.connector, step.pool or connector.pool_name, step.query,
        )

        try:
            result = await connector.execute(step.query, params=step.params or None)
        except QueryNotFoundError as exc:
            raise StepFailed(str(exc)) from exc
        except Exception as exc:
            # SQL errors, statement-not-allowed, write-not-allowed, etc. Don't
            # eat the original traceback; ``raise X from exc`` keeps it visible
            # in tracebacks + log lines.
            raise StepFailed(
                f"sql_query step {step.name!r} on {step.connector}.{step.query} failed: {exc}"
            ) from exc

        # row_count handles SELECT/write asymmetry — SELECT returns len(rows),
        # writes return the driver's rowcount. Either way the operator gets a
        # meaningful "rows affected" number in the Screen.
        return StepResult(rows_affected=result.row_count)
