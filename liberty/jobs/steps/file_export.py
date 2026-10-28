"""``file_export`` step — a named read query written to an xlsx or csv file in the run's work
area, where a ``notify`` step attaches it and the Run detail offers it for download; with
``export_dir`` it is also copied to that folder (a share, a drop folder another system reads).

    [[jobs.steps]]
    type = "file_export"
    name = "export"
    connector = "nomasx1"
    query = "secrev_accounts_get"
    params = { APPS_ID = "${params.apps_id}" }   # binds; the run parameters are bound too
    format = "xlsx"                              # or "csv" (delimiter / encoding as for file_load)
    filename = "accounts-${params.apps_id}"      # default: the query name
    sheet = "Accounts"
    export_dir = "/data/out"                     # optional copy

The rows stream from the database (no in-memory result set), up to the connector's hard cap; a
capped export is flagged in the log and the step's extras. Header row: the columns' labels (the
dictionary's), else their names. Jobs read unrestricted, as for every step.
"""

from __future__ import annotations

import csv
import logging
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from liberty.connectors import ConnectorError, ConnectorRegistry
from liberty.connectors.sql import SQLConnector, StreamDone, StreamMeta, StreamRows
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult
from liberty.jobs.steps.file_load import render_template

_log = logging.getLogger(__name__)


def _binds(step: Step, ctx: RunContext) -> dict[str, Any]:
    """The run parameters (in their case and upper-cased — the queries bind ``:APPS_ID``), then
    the step's ``params`` with their ``${params.x}`` / ``${run.id}`` templates rendered."""
    run = dict(step.op_kwargs)
    out: dict[str, Any] = {}
    for k, v in run.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
            out[k.upper()] = v
    for k, v in step.params.items():
        out[k] = render_template(v, params=run, run_id=ctx.run_id, job_id=ctx.job_id) if isinstance(v, str) else v
    return out


def _cell(v: Any) -> Any:
    """A value openpyxl / csv can write: numbers and dates as they are, the rest as text."""
    if v is None or isinstance(v, (int, float, Decimal, bool, date, datetime)):
        return v
    return str(v)


class FileExportExecutor:
    def __init__(self, connectors: ConnectorRegistry, *, settings: Any = None) -> None:
        self._connectors = connectors
        self._settings = settings

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.FILE_EXPORT:
            raise StepFailed(f"FileExportExecutor cannot run a {step.type.value} step")
        if self._settings is None:
            raise StepFailed("file_export: the app settings are unavailable")
        try:
            conn = self._connectors.get(step.connector or "")
        except ConnectorError as exc:
            raise StepFailed(f"file_export: {exc}") from exc
        if not isinstance(conn, SQLConnector):
            raise StepFailed(f"file_export: {step.connector!r} is not a SQL connector")
        try:
            qdef = conn.get_query(step.query or "")
        except ConnectorError as exc:
            raise StepFailed(f"file_export: {exc}") from exc
        if qdef.writable:
            raise StepFailed(f"file_export: {step.query!r} is a writable query — export a read query")

        from liberty.jobs.files import job_dir, safe_name
        run_params = dict(step.op_kwargs)
        render = lambda s: render_template(s, params=run_params, run_id=ctx.run_id, job_id=ctx.job_id)  # noqa: E731
        fmt = step.format or "xlsx"
        base = safe_name(render(step.filename or step.query or "export")) or "export"
        if not base.lower().endswith(f".{fmt}"):
            base = f"{base}.{fmt}"
        d = job_dir(self._settings, ctx.job_id)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{ctx.run_id}-{base}"
        binds = _binds(step, ctx)

        rows = 0
        truncated = False
        try:
            if fmt == "csv":
                with out.open("w", encoding=step.encoding or "utf-8-sig", newline="") as fh:
                    writer = csv.writer(fh, delimiter=step.delimiter or ";")
                    async for ev in conn.execute_stream(step.query or "", binds, max_rows=conn.HARD_MAX_ROWS):
                        if isinstance(ev, StreamMeta):
                            cols = [c.name for c in ev.columns if not c.hidden]
                            writer.writerow([next((c.label or c.name for c in ev.columns if c.name == n), n) for n in cols])
                        elif isinstance(ev, StreamRows):
                            for r in ev.rows:
                                writer.writerow(["" if r.get(n) is None else _cell(r.get(n)) for n in cols])
                            rows = ev.sent
                        elif isinstance(ev, StreamDone):
                            rows, truncated = ev.total, ev.truncated
            else:
                from openpyxl import Workbook
                wb = Workbook(write_only=True)
                ws = wb.create_sheet(title=str(step.sheet or step.query or "Export")[:31])
                async for ev in conn.execute_stream(step.query or "", binds, max_rows=conn.HARD_MAX_ROWS):
                    if isinstance(ev, StreamMeta):
                        shown = [c for c in ev.columns if not c.hidden]
                        cols = [c.name for c in shown]
                        ws.append([c.label or c.name for c in shown])
                    elif isinstance(ev, StreamRows):
                        for r in ev.rows:
                            ws.append([_cell(r.get(n)) for n in cols])
                        rows = ev.sent
                    elif isinstance(ev, StreamDone):
                        rows, truncated = ev.total, ev.truncated
                wb.save(out)
        except StepFailed:
            raise
        except Exception as exc:  # noqa: BLE001 — a DB / IO error fails the step with its reason
            out.unlink(missing_ok=True)
            raise StepFailed(f"file_export: {step.connector}.{step.query}: {exc}") from exc

        copied: str | None = None
        if step.export_dir:
            target = Path(render(step.export_dir)).expanduser()
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out, target / base)
            except OSError as exc:
                raise StepFailed(f"file_export: cannot copy {base} to {target}: {exc}") from exc
            copied = str(target / base)
        note = f"{step.connector}.{step.query} → {out.name}: {rows} row(s)"
        if truncated:
            note += f" — capped at {conn.HARD_MAX_ROWS}"
            _log.warning("nomaflow.file_export run=%s step=%s capped at %d rows", ctx.run_id, step.name, conn.HARD_MAX_ROWS)
        if copied:
            note += f", copied to {copied}"
        _log.info("nomaflow.file_export run=%s step=%s %s", ctx.run_id, step.name, note)
        return StepResult(rows_affected=rows, log_excerpt=note, extras={"file": out.name, "rows": rows, "format": fmt, "truncated": truncated, **({"copied_to": copied} if copied else {})})
