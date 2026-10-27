"""Map connector-layer exceptions to HTTP responses."""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, status

from liberty.auth.scope import ScopeDeniedError
from liberty.connectors.base import (
    ConnectorError,
    EndpointNotFoundError,
    QueryNotFoundError,
    StatementNotAllowedError,
    UnknownConnectorError,
    UnknownPoolError,
    WriteNotAllowedError,
)

_NOT_FOUND = (UnknownConnectorError, QueryNotFoundError, EndpointNotFoundError)
_UNPROCESSABLE = (StatementNotAllowedError, WriteNotAllowedError)


def summarize_db_error(exc: BaseException) -> str:
    """``"<ExcType>: <driver message>"`` without the ``[SQL: …] [parameters: …]`` tail SQLAlchemy
    appends — that tail carries the statement text *and the bound values*."""
    msg = f"{type(exc).__name__}: {exc}"
    cut = msg.find("[SQL:")  # SQLAlchemy separates the tail with a newline, not a space
    return (msg[:cut] if cut > 0 else msg).strip()


def db_error_detail(
    exc: BaseException, *, context: str, verbose: bool, log: logging.Logger, log_full: bool = False,
) -> str:
    """The ``detail`` string for a failed DB call — and the one place it gets logged.

    What the *caller* gets depends on *verbose*: superusers — the operators who author the
    SQL in Settings and read it back in the error panel — get the full driver message
    (SQLAlchemy's ``[SQL: …] [parameters: …]`` tail included); everyone else gets the driver
    line only. Both carry a short ``error_id`` that also tags the server-log entry.

    What the *log* gets depends on *log_full* — the pool's ``debug_sql`` flag: off (the
    default) logs the driver line + ``error_id``; on logs the statement and the bound values
    too (bound values are data — they stay out of the log unless the operator asked for
    SQL debugging on that pool). Before 7.0.57 every caller with permission on a query
    received its SQL text and bound values in the 502 body.
    """
    error_id = uuid.uuid4().hex[:8]
    full = f"{type(exc).__name__}: {exc}"
    summary = summarize_db_error(exc)
    log.error("%s [error_id=%s]: %s", context, error_id, full if log_full else summary)
    return f"{context}: {full if verbose else summary} (error_id={error_id})"


def http_for_connector_error(exc: ConnectorError) -> HTTPException:
    if isinstance(exc, _NOT_FOUND):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ScopeDeniedError):  # a write outside the caller's row-level data scope
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, _UNPROCESSABLE):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, UnknownPoolError):  # pool not defined / empty URL → service misconfigured
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
