"""Saved analyses on the user-views store: shared vs private, save / save as / delete, the
bundled catalogue sync."""

from __future__ import annotations

import pytest
import pytest_asyncio

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.explorer import parse_explorer
from liberty.explorer.store import KIND_ANALYSIS, KIND_DASHBOARD, SHARED_USER, AnalysisStore, slug, sync_bundled
from liberty.userviews.store import UserViewStore


@pytest_asyncio.fixture
async def views(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'v.db'}")})
    st = UserViewStore(pools, "default")
    await st.create_schema()
    yield st
    await pools.dispose()


def test_slug() -> None:
    assert slug("Qty by pôle / 2026") == "qty-by-pole-2026"
    assert slug("   ") == "analysis" and len(slug("x" * 200)) == 80


@pytest.mark.asyncio
async def test_save_share_save_as_delete(views) -> None:
    st = AnalysisStore(views)
    a = await st.save("shop", KIND_ANALYSIS, "ann", obj_id=None, name="Qty by company", definition={"rows": 1}, audience="sales")
    assert a["id"] == "qty-by-company" and a["owner"] == "ann" and not a["shared"] and a["version"] == 1
    # private: bob does not see it; ann does
    assert await st.list("shop", KIND_ANALYSIS, "bob") == []
    assert [x["id"] for x in await st.list("shop", KIND_ANALYSIS, "ann")] == ["qty-by-company"]
    # replace + share: moves to the shared row, keeps the owner, bumps the version
    a2 = await st.save("shop", KIND_ANALYSIS, "ann", obj_id="qty-by-company", name="Qty by company", definition={"rows": 2}, shared=True)
    assert a2["shared"] and a2["owner"] == "ann" and a2["version"] == 2 and a2["definition"] == {"rows": 2}
    assert [x["id"] for x in await st.list("shop", KIND_ANALYSIS, "bob")] == ["qty-by-company"]
    assert await views.list_views("ann", KIND_ANALYSIS, "shop") == []          # the private row is gone
    # bob saves the shared one: it changes for everyone, the owner stays ann
    b = await st.save("shop", KIND_ANALYSIS, "bob", obj_id="qty-by-company", name="Qty by company (v3)", definition={"rows": 3}, shared=True)
    assert b["owner"] == "ann" and b["name"] == "Qty by company (v3)" and b["version"] == 3
    # save as: a new private copy with a free id
    c = await st.save("shop", KIND_ANALYSIS, "bob", obj_id="qty-by-company", name="Qty by company", definition={"rows": 4}, as_new=True)
    assert c["id"] == "qty-by-company-2" and c["owner"] == "bob" and not c["shared"]
    # delete: bob cannot delete ann's shared one, can delete his own; a superuser can delete anything
    with pytest.raises(PermissionError):
        await st.delete("shop", KIND_ANALYSIS, "bob", "qty-by-company")
    assert await st.delete("shop", KIND_ANALYSIS, "bob", "qty-by-company-2")
    assert await st.delete("shop", KIND_ANALYSIS, "root", "qty-by-company", superuser=True)
    assert await st.list("shop", KIND_ANALYSIS, "ann") == []
    assert not await st.delete("shop", KIND_ANALYSIS, "ann", "ghost")


@pytest.mark.asyncio
async def test_sync_bundled_installs_once_and_refreshes_untouched(views) -> None:
    cfg = {"explorer": {"shop": {"hub": "s", "entities": {"s": {"query": "q"}},
        "analyses": {"qty": {"label": "Quantity", "audience": "sales", "version": 1, "definition": {"rows": ["s.QTY"]}}},
        "dashboards": {"home": {"label": "Home", "definition": {"tiles": [{"analysis": "qty"}]}}}}}}
    f = parse_explorer(cfg)
    assert await sync_bundled(views, f) == {"installed": 2, "refreshed": 0}
    st = AnalysisStore(views)
    items = await st.list("shop", KIND_ANALYSIS, "ann")
    assert items[0]["id"] == "qty" and items[0]["shared"] and items[0]["bundled"] == 1 and items[0]["owner"] == "bundled"
    assert (await st.list("shop", KIND_DASHBOARD, "ann"))[0]["definition"] == {"tiles": [{"analysis": "qty"}]}
    # same version again: nothing
    assert await sync_bundled(views, f) == {"installed": 0, "refreshed": 0}
    # version 2: the untouched copy is refreshed
    cfg["explorer"]["shop"]["analyses"]["qty"]["version"] = 2
    cfg["explorer"]["shop"]["analyses"]["qty"]["definition"] = {"rows": ["s.QTY", "s.CO"]}
    assert await sync_bundled(views, parse_explorer(cfg)) == {"installed": 0, "refreshed": 1}
    assert (await st.list("shop", KIND_ANALYSIS, "ann"))[0]["definition"] == {"rows": ["s.QTY", "s.CO"]}
    # a user edit drops the marker: version 3 leaves it alone; a deleted copy stays deleted
    await st.save("shop", KIND_ANALYSIS, "ann", obj_id="qty", name="Quantity (mine)", definition={"rows": []}, shared=True)
    cfg["explorer"]["shop"]["analyses"]["qty"]["version"] = 3
    assert await sync_bundled(views, parse_explorer(cfg)) == {"installed": 0, "refreshed": 0}
    assert (await st.list("shop", KIND_ANALYSIS, "ann"))[0]["name"] == "Quantity (mine)"
    await st.delete("shop", KIND_DASHBOARD, "root", "home", superuser=True)
    # a deleted bundled copy stays deleted: the marker remembers it was installed
    assert await sync_bundled(views, parse_explorer(cfg)) == {"installed": 0, "refreshed": 0}
    assert await st.list("shop", KIND_DASHBOARD, "ann") == []
    assert [x["name"] for x in await views.list_views(SHARED_USER, KIND_ANALYSIS, "shop")] == ["qty"]
    # a brand-new id in the catalogue is still installed
    cfg["explorer"]["shop"]["analyses"]["rev"] = {"label": "Revenue", "definition": {}}
    assert await sync_bundled(views, parse_explorer(cfg)) == {"installed": 1, "refreshed": 0}
