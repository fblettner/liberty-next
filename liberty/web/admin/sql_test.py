"""``/admin`` — ad-hoc SQL test-run for the per-query editor (split from ``liberty/web/admin.py``)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, status
from liberty.web.admin._base import Superuser, router
from pydantic import BaseModel

_log = logging.getLogger(__name__)


# ── ad-hoc SQL test-run for the per-query editor ──────────────────────────────────────────
class TestSqlBody(BaseModel):
    """Body for ``POST /admin/config/connectors/{c}/test-sql``."""

    sql: str
    params: dict[str, Any] | None = None
    max_rows: int | None = None
    # When True (the default) a write statement is rolled back after capturing the rowcount,
    # so the operator can verify it parses + see "would affect N rows" without mutating the DB.
    # Flip to False from the UI's "Commit" confirmation to actually write.
    dry_run: bool = True


@router.post("/config/connectors/{connector}/test-sql", summary="Test SQL query")
async def test_sql(connector: str, body: TestSqlBody, request: Request, _: Superuser) -> dict[str, Any]:
    """Run a free-form SQL string against *connector*'s pool — powers the SQL editor's Run
    button in the config builder. **Superuser only** (already gated by the ``Superuser`` dep);
    bypasses the per-query ``writable`` flag (the operator is *editing* the query — they need to
    be able to try the write SQL they're typing). The same statement-type allow-list applies
    (``DROP``/``ALTER``/``TRUNCATE`` rejected). With ``dry_run = True`` (default), a write
    runs in a transaction that's rolled back on completion — the rowcount comes back, the DB
    is unchanged. SELECTs ignore ``dry_run`` and return the rows (capped by ``max_rows``).

    The connector must exist and be a SQL connector (404 otherwise — same convention as the
    other routes). A DB error becomes a 502; an unknown ``#SCHEMA.<X>#`` placeholder a 400.
    """
    # Late import — keeps the connectors-related imports out of the auth-only fast path.
    from sqlalchemy.exc import SQLAlchemyError

    from liberty.connectors.base import ConnectorError
    from liberty.web.errors import http_for_connector_error

    connectors = request.app.state.connectors
    try:
        sql_conn = connectors.sql(connector)  # UnknownConnectorError → 404; wrong-type → 404
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    try:
        result = await sql_conn.test_run(
            body.sql, body.params or {}, max_rows=body.max_rows, dry_run=body.dry_run,
        )
    except ConnectorError as exc:
        raise http_for_connector_error(exc) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"query failed: {type(exc).__name__}: {exc}",
        ) from exc
    except Exception as exc:   # a driver error outside SQLAlchemy's (a LOB fetch…): a message, not a bare 500
        _log.exception("test-sql on %s failed", connector)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"query failed: {type(exc).__name__}: {exc}",
        ) from exc
    return result.to_dict()
