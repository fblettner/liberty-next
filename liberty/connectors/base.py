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


def _skip_ws_comments(sql: str, i: int) -> int:
    n = len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif c == "-" and i + 1 < n and sql[i + 1] == "-":  # line comment
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol + 1
        elif c == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    return i


def _read_word(sql: str, i: int) -> tuple[str, int]:
    """Read a ``[A-Za-z_]\\w*`` identifier at *i*; return (UPPERCASED word, end). May be empty."""
    n, start = len(sql), i
    if i < n and (sql[i].isalpha() or sql[i] == "_"):
        i += 1
        while i < n and (sql[i].isalnum() or sql[i] == "_"):
            i += 1
    return sql[start:i].upper(), i


def _skip_balanced_parens(sql: str, i: int) -> int:
    """``sql[i]`` must be ``(``; return the index just after the matching ``)`` (or ``len(sql)``
    if unbalanced). String literals, quoted identifiers, and comments are skipped while counting."""
    n, depth = len(sql), 0
    while i < n:
        c = sql[i]
        if c == "'":  # string literal, '' escapes
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
            j = sql.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol + 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return i
            continue
        i += 1
    return n


def detect_statement_type(sql: str) -> str:
    """Return the upper-cased leading SQL keyword, skipping whitespace + comments.

    For a ``WITH`` (common-table-expression) query, the CTE list is skipped and the
    *main* statement keyword is returned — so ``WITH x AS (...) SELECT ...`` → ``SELECT``
    and ``WITH x AS (...) DELETE FROM y`` → ``DELETE`` (the writable gate then applies
    to the latter). Returns ``""`` when no keyword is found, or ``"WITH"`` if a CTE list
    can't be parsed (which the allow-list then rejects — the safe default).
    """
    if not sql:
        return ""
    i = _skip_ws_comments(sql, 0)
    word, j = _read_word(sql, i)
    if word != "WITH":
        return word

    n = len(sql)
    i = _skip_ws_comments(sql, j)
    w, j2 = _read_word(sql, i)
    if w == "RECURSIVE":
        i = _skip_ws_comments(sql, j2)

    # WITH [RECURSIVE] <name> [(cols)] AS [[NOT] MATERIALIZED] (<body>) [, ...] <main_stmt>
    while i < n:
        if i < n and sql[i] == '"':  # quoted CTE name
            k = sql.find('"', i + 1)
            i = n if k < 0 else k + 1
        else:
            _, i = _read_word(sql, i)
        i = _skip_ws_comments(sql, i)
        if i < n and sql[i] == "(":  # optional column list
            i = _skip_balanced_parens(sql, i)
            i = _skip_ws_comments(sql, i)
        _, i = _read_word(sql, i)  # "AS"
        i = _skip_ws_comments(sql, i)
        w, j3 = _read_word(sql, i)  # optional NOT MATERIALIZED / MATERIALIZED
        if w == "NOT":
            i = _skip_ws_comments(sql, j3)
            w, j3 = _read_word(sql, i)
        if w == "MATERIALIZED":
            i = _skip_ws_comments(sql, j3)
        if not (i < n and sql[i] == "("):  # the CTE body must be here
            return "WITH"
        i = _skip_balanced_parens(sql, i)
        i = _skip_ws_comments(sql, i)
        if i < n and sql[i] == ",":  # another CTE follows
            i = _skip_ws_comments(sql, i + 1)
            continue
        main, _ = _read_word(sql, i)  # the statement after the CTE list
        return main or "WITH"
    return "WITH"


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


def _escape_colons(seg: str) -> str:
    """Backslash a literal colon, skipping one that's already escaped (idempotent)."""
    if ":" not in seg:
        return seg
    out: list[str] = []
    for k, ch in enumerate(seg):
        out.append("\\:" if ch == ":" and (k == 0 or seg[k - 1] != "\\") else ch)
    return "".join(out)


def escape_literal_colons(sql: str) -> str:
    """Backslash-escape every ``:`` inside a single-quoted string literal, double-quoted identifier,
    or SQL comment — the regions where a colon is NEVER a bind parameter.

    SQLAlchemy's ``text()`` bind parser is NOT literal-aware: it reads ``':0'`` as a bind named ``0``
    (and any ``:word`` inside a literal as a bind), which then has no value and raises "A value is
    required for bind parameter ...". The ``\\:`` escape makes ``text()`` emit a plain colon instead
    (the backslash is stripped at compile time). Real ``:name`` binds and ``::type`` casts OUTSIDE
    those regions are left untouched, so this is a no-op for SQL with no colon inside a
    literal/identifier/comment. Mirrors :func:`find_bind_params`' region scan so the two stay in
    lock-step (the contract its docstring promises).
    """
    if ":" not in sql:
        return sql
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":  # single-quoted literal, '' escapes an embedded quote
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(_escape_colons(sql[i:j]))
            i = j
            continue
        if c == '"':  # quoted identifier
            end = sql.find('"', i + 1)
            j = n if end < 0 else end + 1
            out.append(_escape_colons(sql[i:j]))
            i = j
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":  # line comment (to EOL, exclusive)
            eol = sql.find("\n", i)
            j = n if eol < 0 else eol
            out.append(_escape_colons(sql[i:j]))
            i = j
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            end = sql.find("*/", i + 2)
            j = n if end < 0 else end + 2
            out.append(_escape_colons(sql[i:j]))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)
