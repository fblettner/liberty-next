"""Row-level data scope — which **rows** a user may see, on top of which queries they may run.

A role may carry ``scope`` rules (``auth.toml``: ``[roles.<r>] scope = [{column = "COMPANY",
values = ["00001"]}, …]``; the DB backend keeps the same list in ``ly2_roles.scope``). A rule
pins one result column (``column``, matched case-insensitively) or one dictionary entry (``dd``) to
a set of values — literal ``values``, or ``source = "#USER.<attr>#"`` to take them from the user's
``attributes`` (``company``, ``pole``, …) so one role serves every user of every company.
``exclude = true`` turns the list into a black-list (``SY NOT IN ('00')``, nulls kept).

A ``dd`` rule is the one that secures a data item across tables. A result column carries the item
when the screen's hint says ``dd = "CO"``, when the column is named like it (a query aliasing
``ABCO AS CO``), or — on a connector with ``dd_prefix`` (JDE: 2, ``ABCO`` / ``MCCO`` / ``SDCO`` →
``CO``) — when the name minus the table prefix is the item. Every matching column of the query is
bound (a join of F0101 and F0006 restricts both ``ABCO`` and ``MCCO``). A rule may name the
``connector`` it belongs to; on another connector it says nothing (as if absent, never a deny).

Combination (nomaubl's grants model): **roles OR together, columns AND within a role**. A user
with roles R1 ``(company ∈ [A], pole ∈ [X])`` and R2 ``(company ∈ [B])`` sees
``(company = A AND pole = X) OR (company = B)``. A role without scope grants everything, so one
unscoped role makes the user unrestricted. Superusers bypass.

``mode`` says what happens when a query has no such column: ``ignore`` (default — the rule
doesn't apply, the query is unrestricted on that axis) or ``deny`` (that role grants nothing on
this query). A "sales of my company" screen wants ``deny``; a reference list of articles wants
``ignore``.

The scope is resolved at token time (:func:`resolve_scope`) and rides in the JWT as a plain list
of groups, so no DB hit per request; :class:`DataScope` is the runtime form the SQL connector
applies (``_apply_scope_wrap``: ``SELECT * FROM (<sql>) lib_rls WHERE …`` with real binds, and
``check_write`` on updates / deletes). :data:`current_scope` is a contextvar the auth dependency
sets per request so code paths with no principal plumbing (the AI tools, custom reports) are
scoped too; a background job has no request and stays unrestricted by design.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_USER_REF = re.compile(r"^#USER\.([A-Za-z_][A-Za-z0-9_]*)#$")


class ScopeRule(BaseModel):
    """One rule of a role's data scope (the on-disk / on-DB shape)."""

    model_config = ConfigDict(extra="forbid")

    connector: str | None = Field(default=None, description="Apply only to this connector's queries (empty: every connector).")
    column: str | None = Field(default=None, description="Result column the rule applies to (case-insensitive).")
    dd: str | None = Field(default=None, description="Dictionary item — matches every result column carrying it: the hint's dd, the column name, or the name minus the connector's dd_prefix (JDE: ABCO → CO).")
    values: list[str] | None = Field(default=None, description="Allowed values. An empty list allows nothing.")
    source: str | None = Field(default=None, description='Take the values from a user attribute: "#USER.company#".')
    exclude: bool = Field(default=False, description="Hide these values instead of allowing only them (NOT IN; nulls stay visible).")
    mode: Literal["ignore", "deny"] = Field(default="ignore", description="Query without this column: ignore (unrestricted on this axis) or deny (no rows).")

    @model_validator(mode="after")
    def _check(self) -> ScopeRule:
        if not (self.column or self.dd):
            raise ValueError("a scope rule needs `column` or `dd`")
        if (self.values is None) == (self.source is None):
            raise ValueError("a scope rule needs exactly one of `values` / `source`")
        if self.source is not None and not _USER_REF.match(self.source):
            raise ValueError('`source` must look like "#USER.<attribute>#"')
        return self

    @property
    def attribute(self) -> str | None:
        m = _USER_REF.match(self.source or "")
        return m.group(1) if m else None


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v]
    return [str(v)]


def resolve_scope(
    role_scopes: Iterable[Iterable[ScopeRule]], attributes: Mapping[str, Any] | None, *, superuser: bool = False,
) -> list[list[dict[str, Any]]] | None:
    """The wire form of a user's scope: a list of groups (one per scoped role), each a list of
    ``{column, dd, values, mode}`` with the user attributes already substituted. ``None`` =
    unrestricted (superuser, no roles, or at least one role without scope — a role that says
    nothing grants everything)."""
    if superuser:
        return None
    groups: list[list[dict[str, Any]]] = []
    for rules in role_scopes:
        rules = list(rules)
        if not rules:
            return None
        group = []
        for r in rules:
            values = list(r.values) if r.values is not None else _as_list((attributes or {}).get(r.attribute or ""))
            rule: dict[str, Any] = {"column": r.column, "dd": r.dd, "values": values, "mode": r.mode}
            if r.connector:
                rule["connector"] = r.connector
            if r.exclude:
                rule["exclude"] = True
            group.append(rule)
        groups.append(group)
    return groups or None


def normalize_attributes(raw: Mapping[str, Any] | None) -> dict[str, str | list[str]]:
    """User attributes as stored: ``str`` or ``list[str]`` values, empty / None values dropped."""
    out: dict[str, str | list[str]] = {}
    for k, v in (raw or {}).items():
        if v is None or v == "" or v == []:
            continue
        out[str(k)] = [str(x) for x in v] if isinstance(v, (list, tuple, set)) else str(v)
    return out


def apply_claim_attributes(
    current: Mapping[str, Any] | None, claims: Mapping[str, Any], mapping: Mapping[str, str] | None,
) -> dict[str, str | list[str]]:
    """The user's attributes after an OIDC login: every attribute named in *mapping*
    (``{attribute: claim}``, ``[oidc] attribute_claims``) is taken from the ID-token claims —
    set when the claim is present, **removed** when it is absent (the IdP owns those keys, a
    company the IdP no longer asserts must not linger). Other attributes are kept as they are."""
    out: dict[str, Any] = {k: v for k, v in (current or {}).items() if k not in (mapping or {})}
    for attr, claim in (mapping or {}).items():
        if claim in claims:
            out[attr] = claims[claim]
    return normalize_attributes(out)


def merge_attributes(
    current: Mapping[str, Any] | None, changes: Mapping[str, Any], *, replace: bool = False,
) -> dict[str, str | list[str]]:
    """The attributes after an edit: *replace* swaps the whole dict, else *changes* are merged
    over *current* and a ``None`` / empty value removes its key."""
    base: dict[str, Any] = {} if replace else dict(current or {})
    for k, v in changes.items():
        if v is None or v == "" or v == []:
            base.pop(k, None)
        else:
            base[k] = v
    return normalize_attributes(base)


class ScopeDeniedError(Exception):
    """A write targets a row outside the caller's data scope (→ HTTP 403)."""


def column_items(low: str, dd_of: Mapping[str, str], prefix: int = 0) -> set[str]:
    """The dictionary items (lower-cased) a result column *low* may carry: the screen hint's dd when
    there is one, else its own name and — on a ``dd_prefix`` connector — the name minus the
    table prefix (``abco`` → ``co``)."""
    if low in dd_of:
        return {dd_of[low].lower()}
    out = {low}
    if prefix and len(low) > prefix:
        out.add(low[prefix:])
    return out


@dataclass(frozen=True)
class _Rule:
    column: str | None
    dd: str | None
    values: tuple[str, ...]
    mode: str
    connector: str | None = None
    exclude: bool = False

    def allows_value(self, value: Any) -> bool:
        # codes compare trimmed: JDE CHAR(N) values come back blank-padded (or right-justified)
        hit = str(value).strip() in {v.strip() for v in self.values}
        return not hit if self.exclude else hit

    def applies_to(self, connector: str | None) -> bool:
        return not (self.connector and connector and self.connector != connector)

    def match(self, columns: Mapping[str, str], dd_of: Mapping[str, str], prefix: int = 0) -> list[str]:
        """The actual result columns this rule binds to (empty when the query has none).
        *columns* maps lower-cased name → actual name; *dd_of* maps lower-cased name → its dd."""
        found: list[str] = []
        if self.column and self.column.lower() in columns:
            found.append(columns[self.column.lower()])
        if self.dd:
            want = self.dd.lower()
            for low, actual in columns.items():
                if actual not in found and want in column_items(low, dd_of, prefix):
                    found.append(actual)
        return found


@dataclass(frozen=True)
class DataScope:
    """The runtime form: ``groups`` OR together, rules AND within a group."""

    groups: tuple[tuple[_Rule, ...], ...] = field(default_factory=tuple)

    @classmethod
    def from_claims(cls, raw: Any) -> DataScope | None:
        """``None`` when the claim is absent / empty (unrestricted)."""
        if not raw:
            return None
        groups = []
        for g in raw:
            groups.append(tuple(
                _Rule(column=r.get("column"), dd=r.get("dd"), values=tuple(_as_list(r.get("values"))),
                      mode=r.get("mode") or "ignore", connector=r.get("connector") or None,
                      exclude=bool(r.get("exclude")))
                for r in g
            ))
        return cls(groups=tuple(groups)) if groups else None

    def to_claims(self) -> list[list[dict[str, Any]]]:
        out = []
        for g in self.groups:
            group = []
            for r in g:
                d: dict[str, Any] = {"column": r.column, "dd": r.dd, "values": list(r.values), "mode": r.mode}
                if r.connector:
                    d["connector"] = r.connector
                if r.exclude:
                    d["exclude"] = True
                group.append(d)
            out.append(group)
        return out

    def wanted_columns(self) -> set[str]:
        """Lower-cased column names / dd keys the rules refer to (to decide whether the screen's
        hints already tell us which ones the query returns)."""
        out: set[str] = set()
        for g in self.groups:
            for r in g:
                if r.column:
                    out.add(r.column.lower())
                if r.dd:
                    out.add(r.dd.lower())
        return out

    def predicate(
        self, columns: Mapping[str, str], dd_of: Mapping[str, str], *, prefix: int = 0, connector: str | None = None,
        quote=lambda name: name, bind_ref=lambda bind, column: f":{bind}", bind_prefix: str = "rls",
    ) -> tuple[str | None, dict[str, Any]]:
        """``(where_sql, binds)`` for a query of *connector* returning *columns*. ``None`` = no
        restriction applies (every group has a rule that is ignored or no rule at all); ``"1=0"`` =
        nothing visible. Binds are named ``<bind_prefix>_<group>_<rule>_<column>_<value>``;
        :meth:`bind_columns` says which result column each one compares to. *quote* renders the
        column side (the SQL connector wraps blank-padded text in ``TRIM``), *bind_ref* the bind
        side (a ``CAST`` where the driver needs one)."""
        clauses: list[str] = []
        binds: dict[str, Any] = {}
        for gi, group in enumerate(self.groups):
            parts: list[str] = []
            denied = False
            for ri, rule in enumerate(group):
                if not rule.applies_to(connector):
                    continue  # another connector's rule: says nothing here
                cols = rule.match(columns, dd_of, prefix)
                if not cols:
                    if rule.mode == "deny":
                        denied = True
                        break
                    continue  # ignore: this axis is unrestricted for this group
                if not rule.values:
                    if rule.exclude:
                        continue  # nothing excluded: unrestricted on this axis
                    denied = True
                    break
                for ci, col in enumerate(cols):
                    names = []
                    for vi, v in enumerate(rule.values):
                        b = f"{bind_prefix}_{gi}_{ri}_{ci}_{vi}"
                        binds[b] = v
                        names.append(bind_ref(b, col))
                    if rule.exclude:
                        parts.append(f"({quote(col)} IS NULL OR {quote(col)} NOT IN ({', '.join(names)}))")
                    else:
                        parts.append(f"{quote(col)} IN ({', '.join(names)})")
            if denied:
                continue
            if not parts:
                return None, {}  # this role grants everything on this query
            clauses.append("(" + " AND ".join(parts) + ")")
        if not clauses:
            return "1=0", {}
        return " OR ".join(clauses), binds

    def bind_columns(
        self, columns: Mapping[str, str], dd_of: Mapping[str, str], *, prefix: int = 0, connector: str | None = None,
        bind_prefix: str = "rls",
    ) -> dict[str, str]:
        """``{bind_name: result_column}`` for the binds :meth:`predicate` emits (to type each value
        like its column)."""
        out: dict[str, str] = {}
        for gi, group in enumerate(self.groups):
            for ri, rule in enumerate(group):
                if not rule.applies_to(connector):
                    continue
                for ci, col in enumerate(rule.match(columns, dd_of, prefix)):
                    for vi in range(len(rule.values)):
                        out[f"{bind_prefix}_{gi}_{ri}_{ci}_{vi}"] = col
        return out

    def allows_row(
        self, values: Mapping[str, Any], dd_of: Mapping[str, str] | None = None, *, prefix: int = 0,
        connector: str | None = None,
    ) -> bool:
        """Does a row with these column values (keys matched case-insensitively) fall inside the
        scope? Used on writes: the row's scoped columns come from the bound parameters."""
        lower = {str(k).lower(): v for k, v in values.items()}
        columns = {k: k for k in lower}
        for group in self.groups:
            ok = True
            for rule in group:
                if not rule.applies_to(connector):
                    continue
                cols = rule.match(columns, dd_of or {}, prefix)
                if not cols:
                    if rule.mode == "deny":
                        ok = False
                        break
                    continue
                if any(not rule.allows_value(lower[c]) for c in cols):
                    ok = False
                    break
            if ok:
                return True
        return False


current_scope: ContextVar[DataScope | None] = ContextVar("liberty_data_scope", default=None)
# The caller's username for the request, next to its scope — what a tool that saves something on
# the user's behalf (the assistant saving an analysis) signs it with. None outside a request.
current_username: ContextVar[str | None] = ContextVar("liberty_username", default=None)
