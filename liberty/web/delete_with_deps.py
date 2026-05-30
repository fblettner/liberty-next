"""Delete an entity (screen / chart / dashboard) plus a chosen subset of queries that
become orphaned by the delete.

Mirror of :mod:`liberty.web.clone_with_deps` — but destructive, so the safety bar is
higher. The walker only marks a query as "safely deletable" when EVERY reference to it
comes from the entity being deleted. Queries that are also used by another screen / a
dashboard widget / a lookup / etc. are PRESERVED — the operator sees them listed in
the "preserved" report so they know why a query they expected to vanish stuck around.

Use case: customer was experimenting with ``security_users_copy`` (a cloned screen)
plus its cloned queries (``security_users_copy_get`` etc.) and decides to discard the
experiment. Plain delete only removes the screen — the cloned queries linger as
orphans the operator has to clean up by hand. "Delete with queries" removes the screen
AND its dedicated queries in one click.

The actual write happens through the existing admin module's helpers — this file just
produces the plan (entity to delete + safe query list + preserved query list)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from liberty.web.dependencies import Seed, collect_dependencies
from liberty.web.usages import find_usages


@dataclass(slots=True)
class DeletionPlan:
    """What ``plan_delete_with_deps`` recommends to delete + what it preserves."""

    seed: Seed                                          # the entity being deleted
    delete_queries: list[tuple[str, str]] = field(default_factory=list)   # (connector, query_name)
    preserved_queries: list[dict[str, Any]] = field(default_factory=list)  # {connector, name, reason: external usage description}
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": {"kind": self.seed.kind, "name": self.seed.name, "scope": self.seed.scope},
            "delete_queries": [{"connector": c, "name": n} for c, n in self.delete_queries],
            "preserved_queries": list(self.preserved_queries),
            "warnings": list(self.warnings),
        }


def plan_delete_with_deps(state: Any, *, seed: Seed, options: dict[str, bool] | None = None) -> DeletionPlan:
    """Compute the safe deletion plan for *seed*.

    ``options.delete_queries`` (default False): also mark for deletion every query
    *exclusively* used by *seed*. A query is "exclusive" when every usage of it points
    BACK at *seed* (column hints / actions / nested-form tabs / export sheets / chart
    config / dashboard widgets that belong to *seed*). Any external usage — another
    screen, a lookup / sequence, a different dashboard, … — preserves the query and
    records the reason in ``preserved_queries``."""
    opts = options or {}
    plan = DeletionPlan(seed=seed)

    if not opts.get("delete_queries"):
        return plan

    # Closure of the seed → every query it references.
    manifest = collect_dependencies(state, [seed])
    seed_query_keys = [(d.scope or "", d.name) for d in manifest.deps if d.kind == "query"]

    # For each query, find_usages of it. A usage is "internal" (points back at the seed
    # being deleted) iff its deep_link routes to the seed editor with matching scope+name.
    for connector, qname in seed_query_keys:
        if not connector:
            continue
        usages = find_usages(state, kind="query", name=qname, scope=connector)
        external: list[dict[str, Any]] = []
        for u in usages:
            link = u.deep_link
            if _is_usage_on_seed(link, seed):
                continue
            # External — record where so the report explains why the query is kept.
            external.append({"type": u.type, "label": u.label, "deep_link": link})
        if external:
            plan.preserved_queries.append({
                "connector": connector,
                "name": qname,
                "external_usages": external,
                "reason": _summarise_external(external),
            })
        else:
            plan.delete_queries.append((connector, qname))
    return plan


def _is_usage_on_seed(deep_link: dict[str, Any], seed: Seed) -> bool:
    """A usage points at *seed* iff its deep_link routes to the seed's editor with the
    matching scope + name. find_usages encodes deep_link as ``{editor, app/screen/chart/...}``
    — see ``liberty.web.usages._screen_deep_link`` and siblings."""
    editor = deep_link.get("editor")
    if seed.kind == "screen":
        return editor == "screens" and deep_link.get("app") == seed.scope and deep_link.get("screen") == seed.name
    if seed.kind == "chart":
        return editor == "charts" and deep_link.get("scope") == seed.scope and deep_link.get("chart") == seed.name
    if seed.kind == "dashboard":
        # Dashboards aren't scoped consistently — the deep_link only carries ``dashboard``.
        return editor == "dashboards" and deep_link.get("dashboard") == seed.name
    return False


def _summarise_external(external: list[dict[str, Any]]) -> str:
    """One-line summary of why a query is preserved — surfaces the first external
    referrer's label; the UI shows the full list on demand."""
    if not external:
        return ""
    first = external[0]
    suffix = f" (+ {len(external) - 1} more)" if len(external) > 1 else ""
    return f"used by {first.get('label', first.get('type', 'another entity'))}{suffix}"
