"""The Screen Assistant's table presets — ``<config dir>/presets/**.toml``, each file a
``[[presets]]`` list of ``{id, label, description?, group?, connector?, app?, explorer?, tables: [{schema?, name,
label?, cardinality?, alias?, join?, on?: [{left, right}], key?: [item…], query?, query_connector?}]}``. The first table is
the base, the others carry a join + ON on **data items** (``F0101.AN8``) that the consumer resolves
to the physical columns (JDE prefixes them: ``ABAN8`` on F0101); ``key`` names the table's primary
key the same way (``["AN8", "IDLN"]``) — the explorer's entity key, declared once here.

Shared by the assistant's catalogue endpoint and the Data Explorer, whose model can derive its
entities and relations from a preset (``[explorer.<app>.<model>] preset = "<id>"``) — one
declaration of the join graph, shipped in the licensed app bundle.

A preset carrying ``app`` + ``explorer = true`` ships a Data Explorer model by default
(``explorer.<app>.<preset id>``, see :func:`liberty.explorer.config.add_preset_models`): a table's
``label`` is then its entity label and ``cardinality = "one"`` marks a lookup joined to the base
(the default for a joined table is ``many``).

The same files carry the **module catalogues** (``[[modules]]``, catalogue v2): a whole module's
screens, detail tabs and menu — see :mod:`liberty.modules`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def presets_dir(connectors_config_path: Path | str) -> Path:
    return Path(connectors_config_path).parent / "presets"


def load_presets(base: Path | str, connector: str | None = None) -> list[dict[str, Any]]:
    """Every preset under *base* (recursively), filtered by *connector* when given (a preset
    with no ``connector`` matches any). Unreadable files are skipped."""
    base = Path(base)
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.toml")):
        try:
            doc = tomllib.loads(f.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            continue
        for p in doc.get("presets", []) or []:
            if not isinstance(p, dict) or not p.get("id") or not p.get("tables"):
                continue
            if connector and p.get("connector") and p.get("connector") != connector:
                continue
            out.append(p)
    return out


def find_preset(base: Path | str, preset_id: str) -> dict[str, Any] | None:
    for p in load_presets(base):
        if p.get("id") == preset_id:
            return p
    return None
