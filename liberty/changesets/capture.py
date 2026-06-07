"""Bridge the connector write path to change-package capture.

Capture is **cross-DB**: the tracked write commits on its own connector (e.g. ``jdedwards`` /
Oracle) while the package tables live on the *control* pool (Postgres). So capture cannot share the
write's transaction (unlike the same-connector audit mirror) — it runs **after** the write commits,
best-effort: the caller wraps it so a capture failure never fails an already-committed write (it's
logged loudly; the audit tables remain the immutable reconciliation source).

The ``application`` key for the package scope is the **connector name** (one connector = one
environment). The natural key + pre-image come from the bound params the route already has:
``:_ORIGINAL`` binds are the pre-image (old state, for drift detection on promotion); the uppercase
non-``_ORIGINAL`` binds are the new state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from liberty.changesets.store import capture as _store_capture

_WRITE_OPS = {"INSERT", "UPDATE", "DELETE"}
_ORIG = "_ORIGINAL"


def _jsonable(v: Any) -> Any:
    """Coerce a resolved bind value into something the JSON ``new_values`` / ``old_values`` columns
    accept. The capture now records the FULL post-rule bind set, which can include Python
    ``datetime`` (a SYSDATE stamp on a non-JDE column) or ``Decimal`` (a numeric column) — neither
    is JSON-native. JDE audit dates/times are already ``jdedate``/``jdetime`` *ints*, so this is a
    safety net for the general case. Binary values can't round-trip through a textual change entry,
    so they're dropped to ``None``."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    return v


def split_params(params: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split bound params into ``(new_values, old_values)`` — same convention the audit mirror uses:
    uppercase, non-``_ORIGINAL`` binds are the new row; ``:<COL>_ORIGINAL`` binds are the pre-image
    (suffix stripped). Lowercase / internal binds (``_aud_*``, filter ``*_op``) are ignored. Values
    are JSON-normalised (the captured binds are the resolved ones, which may be datetime/Decimal)."""
    new_values: dict[str, Any] = {}
    old_values: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if not k or not k.isupper():
            continue
        if k.endswith(_ORIG):
            old_values[k[: -len(_ORIG)]] = _jsonable(v)
        else:
            new_values[k] = _jsonable(v)
    return new_values, old_values


def entity_key(new_values: dict[str, Any], old_values: dict[str, Any], key_columns: list[str]) -> dict[str, Any]:
    """Natural key from the screen's ``key_columns`` — prefer the pre-image (``old_values``) so the
    key is the row's *stable* identity across an insert→update→delete sequence (compaction groups on
    it); fall back to ``new_values`` (inserts and key-less deletes have no pre-image)."""
    key: dict[str, Any] = {}
    for kc in key_columns or []:
        k = kc.upper()
        if k in old_values:
            key[k] = old_values[k]
        elif k in new_values:
            key[k] = new_values[k]
    return key


async def capture_write(
    db: Any,  # ChangeSetDatabase
    *,
    connector: str,
    query: str,
    statement_type: str | None,
    params: dict[str, Any] | None,
    user: str | None,
    key_columns: list[str] | None,
    read_query: str | None,
    entity: str | None,
    change_tracked: bool = True,
) -> str | None:
    """Capture a committed write into the connector's current package. The caller supplies the
    write target's ``key_columns`` (for the natural key + drift pre-image), its ``read_query``
    (full-pre-image drift on apply; ``None`` when the target has no standalone read — e.g. a 1:1
    column-group table that's only JOINed into the screen's read), and the display ``entity``.

    A SINGLE screen Save can produce several captures: the main table plus each 1:1 column-group
    table it writes — each lands as its own entry with its own key, so compaction nets them per
    physical table (not collapsed together just because they share the parent row's key).

    Returns the entry id, or ``None`` when it's not a write / not tracked. Raises on capture
    failure — the caller logs + swallows so the committed write isn't undone."""
    op = (statement_type or "").upper()
    if op not in _WRITE_OPS:
        return None
    if not change_tracked:
        return None
    new_values, old_values = split_params(params)
    ekey = entity_key(new_values, old_values, key_columns or [])
    return await _store_capture(
        db,
        connector,  # application = connector name
        connector=connector,
        query=query,
        operation=op,
        entity=entity,
        entity_key=ekey or None,
        new_values=new_values or None,
        old_values=old_values or None,
        read_query=read_query or None,
        user=user,
    )
