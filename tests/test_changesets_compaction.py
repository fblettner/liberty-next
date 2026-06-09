"""Compaction — collapse a package's per-row history to the minimal net op for promotion."""

from __future__ import annotations

from liberty.changesets.compaction import compact
from liberty.changesets.models import ChangeEntry, EntryStatus, Operation


def _e(seq, op, key, new=None, old=None, *, connector="jde", query=None, entity="security", status="captured"):
    return ChangeEntry(
        id=f"e{seq}", package_id="p", seq=seq, connector=connector,
        query=query or f"f_{op.lower()}", operation=op, entity=entity,
        entity_key=key, new_values=new, old_values=old, status=status,
    )


def test_insert_then_update_compacts_to_one_insert_with_final_values() -> None:
    entries = [
        _e(1, Operation.INSERT.value, {"K": "1"}, new={"K": "1", "NAME": "a"}, query="f_post"),
        _e(2, Operation.UPDATE.value, {"K": "1"}, new={"K": "1", "NAME": "b"}, old={"K": "1", "NAME": "a"}, query="f_put"),
    ]
    out = compact(entries)
    assert len(out) == 1
    assert out[0]["operation"] == "INSERT" and out[0]["query"] == "f_post"
    assert out[0]["new_values"] == {"K": "1", "NAME": "b"}   # final values
    assert out[0]["old_values"] is None                       # created → no pre-image


def test_insert_then_delete_is_dropped() -> None:
    entries = [
        _e(1, Operation.INSERT.value, {"K": "1"}, new={"K": "1"}, query="f_post"),
        _e(2, Operation.DELETE.value, {"K": "1"}, new={"K": "1"}, query="f_delete"),
    ]
    assert compact(entries) == []   # created and removed within the package


def test_update_then_delete_compacts_to_delete_keyed_by_pre_image() -> None:
    entries = [
        _e(1, Operation.UPDATE.value, {"K": "1"}, new={"K": "1", "NAME": "b"}, old={"K": "1", "NAME": "a"}, query="f_put"),
        _e(2, Operation.DELETE.value, {"K": "1"}, new={"K": "1"}, query="f_delete"),
    ]
    out = compact(entries)
    assert len(out) == 1 and out[0]["operation"] == "DELETE" and out[0]["query"] == "f_delete"
    assert out[0]["old_values"] == {"K": "1", "NAME": "a"}   # pre-image from the FIRST op → drift check


def test_update_only_keeps_pre_image_and_final() -> None:
    entries = [
        _e(1, Operation.UPDATE.value, {"K": "1"}, new={"K": "1", "N": "b"}, old={"K": "1", "N": "a"}),
        _e(2, Operation.UPDATE.value, {"K": "1"}, new={"K": "1", "N": "c"}, old={"K": "1", "N": "b"}),
    ]
    out = compact(entries)
    assert len(out) == 1 and out[0]["operation"] == "UPDATE"
    assert out[0]["new_values"] == {"K": "1", "N": "c"}      # final
    assert out[0]["old_values"] == {"K": "1", "N": "a"}      # earliest pre-image


def test_distinct_rows_and_excluded() -> None:
    entries = [
        _e(1, Operation.INSERT.value, {"K": "1"}, new={"K": "1"}, query="f_post"),
        _e(2, Operation.INSERT.value, {"K": "2"}, new={"K": "2"}, query="f_post", status=EntryStatus.EXCLUDED.value),
        _e(3, Operation.UPDATE.value, {"K": "3"}, new={"K": "3"}, old={"K": "3"}),
    ]
    out = compact(entries)
    keys = [o["entity_key"]["K"] for o in out]
    assert keys == ["1", "3"]   # K=2 excluded → omitted; order follows capture


def test_invocation_entries_pass_through_verbatim_and_never_merge() -> None:
    """CALL_API / CALL_PLUGIN entries are screen-action replays — not row diffs. Compaction emits
    them one-for-one in capture order and never collapses them (no natural key)."""
    entries = [
        _e(1, Operation.INSERT.value, {"K": "1"}, new={"K": "1", "N": "a"}, query="f_post"),
        ChangeEntry(id="e2", package_id="p", seq=2, connector="ldap", query="sync_user",
                    operation=Operation.CALL_API.value, entity="user", entity_key=None,
                    new_values={"USR_ID": "DEMO3"}, old_values=None, status="captured"),
        ChangeEntry(id="e3", package_id="p", seq=3, connector="jde", query="nomajde.security:j_remerge",
                    operation=Operation.CALL_PLUGIN.value, entity="user", entity_key=None,
                    new_values={"app": "P0092"}, old_values=None, status="captured"),
    ]
    out = compact(entries)
    assert [o["operation"] for o in out] == ["INSERT", "CALL_API", "CALL_PLUGIN"]   # order preserved
    api_op = out[1]
    assert api_op["connector"] == "ldap" and api_op["query"] == "sync_user"
    assert api_op["new_values"] == {"USR_ID": "DEMO3"} and api_op["entity_key"] is None
    assert out[2]["query"] == "nomajde.security:j_remerge" and out[2]["new_values"] == {"app": "P0092"}


def test_main_and_group_writes_sharing_a_key_stay_distinct() -> None:
    """Two physical tables written under one screen Save share the parent key (ULUSER) but must NOT
    merge — the table token (query minus its op suffix) keeps them apart."""
    entries = [
        _e(1, Operation.INSERT.value, {"ULUSER": "DEMO3"}, new={"ULUSER": "DEMO3"}, query="f0092_post", entity="user"),
        _e(2, Operation.INSERT.value, {"ULUSER": "DEMO3"}, new={"ULUSER": "DEMO3", "FMT": "DMY"}, query="f00921_post", entity="user"),
    ]
    out = compact(entries)
    assert [o["query"] for o in out] == ["f0092_post", "f00921_post"]   # both kept, order preserved
