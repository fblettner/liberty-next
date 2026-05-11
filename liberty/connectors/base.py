"""Shared connector primitives: exceptions and the small SQL text scanner.

The SQL scanner is a direct port of nomaubl's ``SqlConnectorClient`` state
machine. We do *not* rewrite ``:name`` tokens here — SQLAlchemy's ``text()``
construct already binds named parameters safely. We only need to (a) detect the
leading statement keyword for the allow-list / writable gate and (b) collect the
set of ``:name`` tokens so optional filters can be auto-bound to SQL ``NULL``.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for all connector-layer errors."""


class UnknownConnectorError(ConnectorError):
    """Raised when a connector name is not present in the registry."""


class UnknownPoolError(ConnectorError):
    """Raised when a SQL connector references a pool that was never defined."""


class QueryNotFoundError(ConnectorError):
    """Raised when a named query does not exist on a SQL connector."""


class EndpointNotFoundError(ConnectorError):
    """Raised when a named endpoint does not exist on an API connector."""


class StatementNotAllowedError(ConnectorError):
    """Raised when a query's leading keyword is outside the allow-list."""


class WriteNotAllowedError(ConnectorError):
    """Raised when a mutating statement runs against a query without ``writable``."""


# Statement keywords the SQL connector is willing to dispatch.
ALLOWED_STATEMENTS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"})

# Subset of :data:`ALLOWED_STATEMENTS` that mutate data — only permitted when the
# query opts in via ``writable = true``.
WRITE_STATEMENTS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})


def detect_statement_type(sql: str) -> str:
    """Return the upper-cased leading SQL keyword, skipping whitespace + comments.

    Returns an empty string when no keyword can be found.
    """
    if not sql:
        return ""
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":  # line comment
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol + 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        end = i
        while end < n and sql[end].isalpha():
            end += 1
        return sql[i:end].upper()
    return ""


def find_bind_params(sql: str) -> list[str]:
    """Collect ``:name`` parameter tokens in *sql*, in first-appearance order.

    Tokens inside single-quoted string literals, double-quoted identifiers, line
    comments, block comments, and the PostgreSQL ``::type`` cast operator are
    ignored — matching SQLAlchemy's own ``text()`` parsing closely enough that
    the two never disagree on real-world queries.
    """
    names: list[str] = []
    seen: set[str] = set()
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":  # single-quoted literal, '' escapes an embedded quote
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == '"':  # quoted identifier
            end = sql.find('"', i + 1)
            i = n if end < 0 else end + 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":  # line comment
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c == ":" and i + 1 < n and sql[i + 1] == ":":  # ::type cast
            i += 2
            continue
        if c == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            end = i + 1
            while end < n and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            name = sql[i + 1 : end]
            if name not in seen:
                seen.add(name)
                names.append(name)
            i = end
            continue
        i += 1
    return names
