"""Map connector-layer exceptions to HTTP responses."""

from __future__ import annotations

from fastapi import HTTPException, status

from liberty.connectors.base import (
    ConnectorError,
    EndpointNotFoundError,
    QueryNotFoundError,
    StatementNotAllowedError,
    UnknownConnectorError,
    UnknownPoolError,
    WriteNotAllowedError,
)

_NOT_FOUND = (UnknownConnectorError, QueryNotFoundError, EndpointNotFoundError, UnknownPoolError)
_UNPROCESSABLE = (StatementNotAllowedError, WriteNotAllowedError)


def http_for_connector_error(exc: ConnectorError) -> HTTPException:
    if isinstance(exc, _NOT_FOUND):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, _UNPROCESSABLE):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
