"""Built-in NomaFlow job callables for framework housekeeping.

Unlike the per-application callables (``nomasx1.security:…`` etc.) that live in the plugins tree,
these ship with the framework, so a ``jobs.toml`` can reference them on any install without a custom
plugin package:

    [[jobs]]
    id = "purge-config-versions"
    schedule = "0 3 * * *"
    [[jobs.steps]]
    type = "python"
    name = "purge"
    callable = "liberty.jobs.maintenance:purge_config_versions"
    [jobs.steps.op_kwargs]
    max_versions = 20
    max_age_days = 90

The python-step executor injects ``settings`` / ``ctx`` by name (see
:mod:`liberty.jobs.steps.python_step`); the retention knobs come from the step's ``op_kwargs`` (and
are editable in the NomaFlow job editor like any other param).
"""
from __future__ import annotations

import logging
from typing import Any

from liberty.config import Settings
from liberty.versioning import ConfigVersionStore

_log = logging.getLogger(__name__)


def purge_config_versions(
    settings: Settings,
    *,
    ctx: Any = None,
    max_versions: int = 20,
    max_age_days: int = 90,
) -> int:
    """Purge old config-version snapshots (file history + screen bundles) per a retention policy.

    Removes a version when it falls beyond the ``max_versions`` most-recent for its key OR predates
    ``now - max_age_days`` — the newest version of each key is always kept, so history never empties.
    A knob ``<= 0`` disables that rule. Returns the number of snapshots removed (the step's
    ``rows_affected``).

    The store is filesystem-backed (``.versions/`` next to the config TOMLs), so this reconstructs a
    :class:`ConfigVersionStore` over the same dir the web layer uses — operating on the same SQLite
    index + snapshot files."""
    base = settings.connectors.config_path.parent
    store = ConfigVersionStore(base)
    removed = store.purge(max_versions=max_versions, max_age_days=max_age_days)
    run = getattr(ctx, "run_id", None)
    _log.info(
        "nomaflow.maintenance purge_config_versions run=%s removed=%s (max_versions=%s max_age_days=%s)",
        run, removed, max_versions, max_age_days,
    )
    return removed
