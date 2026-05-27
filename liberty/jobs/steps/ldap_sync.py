"""``ldap_sync`` executor — paged LDAP search → per-row write into a SQL target.

The v2 replacement for v1's ``j_ldap`` (see
``legacy/airflow-plugins-enterprise/.../nomasx1/agent/ldap.py``). v1 hard-coded the
AD attribute set + the SECURITY_LDAP destination + Spark's JDBC writer. v2 separates
the two halves: this executor *only* knows how to fan out a paged LDAP search and
push each entry through a named query on a SQL connector. The DDL of the target
table, the column list, the row dedupe strategy — all live in ``connectors.toml`` as
a writable query the operator (or a preceding ``sql_query`` step) controls.

The split keeps the executor reusable across LDAP shapes (AD, OpenLDAP, FreeIPA)
and connector dialects (Postgres, Oracle, SQLite) without baking either knowledge
into Python.

Schema (see :class:`~liberty.jobs.schema.Step`):

* ``server`` — full URL, e.g. ``ldaps://ad.example.com:636`` or ``ldap://...``
* ``bind_dn`` / ``bind_password`` — credentials; ``${ENV}`` substitution happens at
  jobs.toml load time so secrets stay out of source control.
* ``search_base`` — DN to search under, e.g. ``DC=example,DC=com``
* ``search_filter`` — RFC 4515 filter, e.g. ``(objectClass=user)``
* ``attributes`` — LDAP attribute names to request (server-side projection).
* ``target_connector`` / ``target_query`` — where to write each row. The query
  is invoked once per LDAP entry with the param dict built from ``mapping``.
* ``mapping`` — ``{ldap_attr: query_param}``. For each entry: every key listed in
  ``mapping`` is read off the LDAP entry, the value becomes the query param bound
  to the mapped name. Missing attributes resolve to ``None`` (matches v1's
  ``entry.X.value or ""`` semantics but as a SQL NULL instead of empty string —
  cleaner for downstream filtering / outer joins).

Failure modes (all become :class:`StepFailed`):

* LDAP bind fails (wrong credentials, server down, TLS error)
* Target connector / query doesn't exist
* The target query isn't writable (``writable = true`` missing)
* Any single row write fails — the executor aborts at the first failure and
  reports row N (so the operator knows how far the import got)

Per-row writes go through :meth:`SQLConnector.execute`, which gives us the full
filter wrap / form rules / audit-table machinery for free at the cost of one
round-trip per entry. For typical LDAP volumes (sub-10k entries) that's fine;
the volume case is rare enough that the simplicity win beats a bulk-insert
fast-path. If it ever becomes a bottleneck, swap in an executemany path here
without touching the runner contract.

Concurrency: the ``ldap3`` library is sync-only, so the search runs in
:func:`asyncio.to_thread` to avoid stalling the scheduler loop. The per-row
writes are async (they go through the existing connector path).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from liberty.connectors import (
    ConnectorRegistry,
    QueryNotFoundError,
    UnknownConnectorError,
    WriteNotAllowedError,
)
from liberty.connectors.sql import SQLConnector
from liberty.jobs.schema import Step, StepType
from liberty.jobs.steps.base import RunContext, StepFailed, StepResult

_log = logging.getLogger(__name__)


class LdapSyncExecutor:
    """Executes one ``ldap_sync`` step. Stateless — same instance can run any
    number of concurrent steps; each opens its own LDAP connection."""

    def __init__(self, connectors: ConnectorRegistry) -> None:
        self._connectors = connectors

    async def execute(self, step: Step, ctx: RunContext) -> StepResult:
        if step.type is not StepType.LDAP_SYNC:
            raise StepFailed(
                f"LdapSyncExecutor received a step of type {step.type.value!r} — "
                "the runner wired the wrong executor for this step type"
            )
        # Defensive — the schema validator caught these at parse time, but a
        # hand-built Step from a test could slip through.
        for field in ("server", "bind_dn", "search_base", "target_connector", "target_query"):
            if not getattr(step, field):
                raise StepFailed(
                    f"ldap_sync step {step.name!r}: missing required field {field!r}"
                )

        target = self._resolve_target(step)

        _log.info(
            "nomaflow.ldap_sync start run=%s step=%r server=%s base=%s filter=%s",
            ctx.run_id, step.name, step.server, step.search_base, step.search_filter or "(objectClass=*)",
        )

        # LDAP search hops to a worker thread (ldap3 is sync). The result is a
        # list of plain row-dicts ready for the target query; the SQL write half
        # is async on this thread so the connector + audit machinery still run
        # on the event loop.
        rows = await asyncio.to_thread(_search_ldap, step)

        _log.info(
            "nomaflow.ldap_sync run=%s step=%r entries=%d → writing to %s.%s",
            ctx.run_id, step.name, len(rows), step.target_connector, step.target_query,
        )

        written = 0
        for idx, row in enumerate(rows, start=1):
            try:
                await target.execute(step.target_query, params=row)  # type: ignore[arg-type]
            except WriteNotAllowedError as exc:
                # First write tells us the query isn't writable — no point trying the rest.
                raise StepFailed(
                    f"ldap_sync step {step.name!r}: target query {step.target_connector}.{step.target_query} "
                    f"is not marked writable (set `writable = true` on the query definition): {exc}"
                ) from exc
            except QueryNotFoundError as exc:
                raise StepFailed(str(exc)) from exc
            except Exception as exc:
                raise StepFailed(
                    f"ldap_sync step {step.name!r}: row {idx}/{len(rows)} failed to write to "
                    f"{step.target_connector}.{step.target_query}: {exc}"
                ) from exc
            written += 1

        return StepResult(
            rows_affected=written,
            extras={"ldap_entries": len(rows), "rows_written": written},
        )

    # -- internals ------------------------------------------------------- #

    def _resolve_target(self, step: Step) -> SQLConnector:
        """Look up the target connector + assert it's a SQL one. Mirrors the
        same shape every other SQL-touching executor uses, so wiring errors
        surface with the same message no matter which step type triggered them."""
        assert step.target_connector is not None  # narrowed by guard above
        try:
            target = self._connectors.get(step.target_connector)
        except UnknownConnectorError as exc:
            raise StepFailed(str(exc)) from exc
        if not isinstance(target, SQLConnector):
            raise StepFailed(
                f"ldap_sync step {step.name!r}: target_connector {step.target_connector!r} "
                f"is not a SQL connector (got {type(target).__name__})"
            )
        return target


def _search_ldap(step: Step) -> list[dict[str, Any]]:
    """Run the paged LDAP search synchronously + return a list of param-dicts
    ready for the target query.

    Lives at module scope (not inside the class) so it pickles cleanly across
    the to_thread boundary + so tests can stub it without monkey-patching the
    executor class.

    The mapping resolution: for every ``mapping`` entry ``ldap_attr → param``, the
    LDAP entry's attribute value is read and stored under ``param``. Multi-valued
    attributes return their first value (the common case — sAMAccountName, mail,
    displayName are all single-valued in practice). Missing attributes become
    ``None`` (SQL NULL) — *not* empty string — so target columns can use NOT NULL
    for "must be present" semantics.
    """
    # Local import — ldap3 only when an ldap_sync step actually runs; keeps the
    # base import graph free of the ldap3 module for tests + deployments that
    # never use this executor.
    from ldap3 import ALL, SUBTREE, Connection, Server

    server = Server(step.server, get_info=ALL)
    conn = Connection(
        server,
        user=step.bind_dn,
        password=step.bind_password,
        auto_bind=True,
    )
    try:
        # ``attributes`` may be empty when the operator only cares about the
        # entry's DN — pass ``None`` to ldap3 in that case so it returns the
        # default attribute set rather than an empty selection that would fail
        # the search.
        attr_list = step.attributes or None
        conn.search(
            search_base=step.search_base,
            search_filter=step.search_filter or "(objectClass=*)",
            search_scope=SUBTREE,
            paged_size=1000,
            attributes=attr_list,
        )
        rows: list[dict[str, Any]] = []
        for entry in conn.entries:
            row: dict[str, Any] = {}
            # When mapping is empty the operator wants every requested attribute
            # bound under its own name (matches the obvious default — no need to
            # spell out a 1:1 mapping for the common case).
            iter_mapping = (
                step.mapping.items()
                if step.mapping
                else ((a, a) for a in (attr_list or []))
            )
            for ldap_attr, param_name in iter_mapping:
                row[param_name] = _read_attr(entry, ldap_attr)
            rows.append(row)
        return rows
    finally:
        if conn.bound:
            conn.unbind()


def _read_attr(entry: Any, name: str) -> Any:
    """Read one attribute value off an ldap3 entry, returning ``None`` when the
    attribute is missing or empty. ldap3 raises ``LDAPKeyError`` for unknown
    attribute names — we catch it because a *missing* attribute is a normal
    sparse-attribute case (not every user has a manager / mobile / etc.), not
    an error worth aborting the import."""
    try:
        value = entry[name].value
    except Exception:
        return None
    # ldap3 returns ``[]`` for an empty multi-valued attribute and the bare
    # value (str / int / datetime) for single-valued ones. Normalise both: an
    # empty list → None; a non-empty list → first element; bare values pass through.
    if value == "" or value is None:
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value
