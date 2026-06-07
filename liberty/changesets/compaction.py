"""Compact a package's captured entries into the minimal NET change per row, for export / apply.

Capture keeps the full history (every INSERT/UPDATE/DELETE in order — the audit trail). But replaying
that verbatim in production would re-run redundant work (an add-then-update writes twice) or even
write a row that the package later deletes. Compaction collapses the per-row history to one net op:

    INSERT … UPDATE*            → one INSERT with the FINAL values (created in this package)
    INSERT … DELETE             → dropped entirely (created and removed within the package → no-op)
    UPDATE* (row pre-existing)  → one UPDATE with the final values + the FIRST op's pre-image
    UPDATE* … DELETE            → one DELETE (pre-existing row removed), keyed by the pre-image

Rows are grouped by ``(connector, entity, natural key)``. Entries with no natural key can't be
grouped, so each stands alone (no merge). Excluded entries are skipped. Output order follows first
capture so dependencies (a role before its assignment) replay correctly.
"""

from __future__ import annotations

import json
from typing import Any

from liberty.changesets.models import ChangeEntry, EntryStatus, Operation


def _row_key(e: ChangeEntry) -> str:
    """Stable identity for the affected row — ``connector | entity | sorted(natural key)``. Falls
    back to the entry id when there's no natural key, so keyless entries never merge."""
    if not e.entity_key:
        return f"__nokey__:{e.id}"
    key = json.dumps({str(k).upper(): e.entity_key[k] for k in sorted(e.entity_key)}, default=str, sort_keys=True)
    return f"{e.connector}|{e.entity or ''}|{key}"


def compact(entries: list[ChangeEntry]) -> list[dict[str, Any]]:
    """Collapse *entries* to the net op per row (see module docstring). Returns a list of plain
    dicts (replay-ready): ``connector, query, operation, entity, entity_key, new_values,
    old_values, source_ids``. INSERT-then-DELETE rows are omitted."""
    included = [e for e in entries if e.status != EntryStatus.EXCLUDED.value]
    included.sort(key=lambda e: e.seq)

    groups: dict[str, list[ChangeEntry]] = {}
    order: list[str] = []
    for e in included:
        k = _row_key(e)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(e)

    out: list[dict[str, Any]] = []
    for k in order:
        ops = groups[k]
        created = ops[0].operation == Operation.INSERT.value
        deleted = ops[-1].operation == Operation.DELETE.value
        if created and deleted:
            continue  # created and removed within the package — nothing to promote

        def _pick(op: str) -> ChangeEntry | None:
            return next((e for e in ops if e.operation == op), None)

        if deleted:
            d = _pick(Operation.DELETE.value) or ops[-1]
            first = ops[0]
            out.append({
                "connector": d.connector, "query": d.query, "operation": Operation.DELETE.value,
                "entity": d.entity, "entity_key": d.entity_key,
                "new_values": None, "old_values": first.old_values,
                "source_ids": [e.id for e in ops],
            })
            continue

        # No delete → the row ends in a written state. Its final values are the last write's.
        last_write = next((e for e in reversed(ops) if e.operation != Operation.DELETE.value), ops[-1])
        if created:
            ins = _pick(Operation.INSERT.value) or ops[0]
            out.append({
                "connector": ins.connector, "query": ins.query, "operation": Operation.INSERT.value,
                "entity": ins.entity, "entity_key": last_write.entity_key,
                "new_values": last_write.new_values, "old_values": None,
                "source_ids": [e.id for e in ops],
            })
        else:
            upd = _pick(Operation.UPDATE.value) or last_write
            first = ops[0]
            out.append({
                "connector": upd.connector, "query": upd.query, "operation": Operation.UPDATE.value,
                "entity": upd.entity, "entity_key": last_write.entity_key,
                "new_values": last_write.new_values, "old_values": first.old_values,
                "source_ids": [e.id for e in ops],
            })
    return out
