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

from typing import Any

from liberty.changesets.store import capture as _store_capture

_WRITE_OPS = {"INSERT", "UPDATE", "DELETE"}
_ORIG = "_ORIGINAL"


def split_params(params: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split bound params into ``(new_values, old_values)`` — same convention the audit mirror uses:
    uppercase, non-``_ORIGINAL`` binds are the new row; ``:<COL>_ORIGINAL`` binds are the pre-image
    (suffix stripped). Lowercase / internal binds (``_aud_*``, filter ``*_op``) are ignored."""
    new_values: dict[str, Any] = {}
    old_values: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if not k or not k.isupper():
            continue
        if k.endswith(_ORIG):
            old_values[k[: -len(_ORIG)]] = v
        else:
            new_values[k] = v
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
    screen: Any,
    user: str | None,
) -> str | None:
    """Capture a committed write into the connector's current package when the screen opts in
    (``change_tracked``). Returns the entry id, or ``None`` when it's not a write / not tracked.
    Raises on capture failure — the caller logs + swallows so the committed write isn't undone."""
    op = (statement_type or "").upper()
    if op not in _WRITE_OPS:
        return None
    if not getattr(screen, "change_tracked", False):
        return None
    new_values, old_values = split_params(params)
    ekey = entity_key(new_values, old_values, getattr(screen, "key_columns", None) or [])
    return await _store_capture(
        db,
        connector,  # application = connector name
        connector=connector,
        query=query,
        operation=op,
        entity=getattr(screen, "change_entity", None),
        entity_key=ekey or None,
        new_values=new_values or None,
        old_values=old_values or None,
        # Read query for the written table — drives full pre-image drift detection on apply.
        read_query=getattr(screen, "read_query", None) or None,
        user=user,
    )
