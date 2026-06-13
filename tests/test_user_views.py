"""Tests for the per-user saved-view store (liberty.userviews)."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from liberty.connectors.db import PoolRegistry
from liberty.userviews import UserViewStore


@pytest.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'views.db'}")
    pools = PoolRegistry()
    pools.register_engine("default", engine)
    st = UserViewStore(pools, "default")
    await st.create_schema()
    await st.create_schema()  # idempotent — second call is a no-op
    return st


@pytest.mark.asyncio
async def test_save_list_roundtrip(store: UserViewStore) -> None:
    assert await store.list_views("alice", "grid", "app::scr::g") == []
    fmt = {"columns": ["A", "B"], "sort": [{"id": "A", "desc": False}], "group_by": []}
    await store.save_view("alice", "grid", "app::scr::g", "Wide", fmt)
    views = await store.list_views("alice", "grid", "app::scr::g")
    assert len(views) == 1
    assert views[0]["name"] == "Wide"
    assert views[0]["payload"] == fmt
    assert views[0]["updated_at"]


@pytest.mark.asyncio
async def test_multiple_named_views_per_key(store: UserViewStore) -> None:
    await store.save_view("alice", "grid", "k", "Wide", {"columns": ["A", "B", "C"]})
    await store.save_view("alice", "grid", "k", "Narrow", {"columns": ["A"]})
    views = await store.list_views("alice", "grid", "k")
    assert {v["name"] for v in views} == {"Wide", "Narrow"}
    # ordered by name
    assert [v["name"] for v in views] == ["Narrow", "Wide"]


@pytest.mark.asyncio
async def test_save_upserts_by_name(store: UserViewStore) -> None:
    await store.save_view("alice", "grid", "k", "V", {"columns": ["A"]})
    await store.save_view("alice", "grid", "k", "V", {"columns": ["A", "B", "C"]})
    views = await store.list_views("alice", "grid", "k")
    assert len(views) == 1 and views[0]["payload"] == {"columns": ["A", "B", "C"]}


@pytest.mark.asyncio
async def test_delete_one_named_view(store: UserViewStore) -> None:
    await store.save_view("alice", "grid", "k", "A", {"x": 1})
    await store.save_view("alice", "grid", "k", "B", {"x": 2})
    assert await store.delete_view("alice", "grid", "k", "A") is True
    assert {v["name"] for v in await store.list_views("alice", "grid", "k")} == {"B"}
    assert await store.delete_view("alice", "grid", "k", "A") is False  # already gone


@pytest.mark.asyncio
async def test_isolation_by_user_kind_and_key(store: UserViewStore) -> None:
    # Same key+name, different users → independent.
    await store.save_view("alice", "grid", "k", "V", {"u": "a"})
    await store.save_view("bob", "grid", "k", "V", {"u": "b"})
    assert (await store.list_views("alice", "grid", "k"))[0]["payload"] == {"u": "a"}
    assert (await store.list_views("bob", "grid", "k"))[0]["payload"] == {"u": "b"}
    # Same user+key+name, different kind → independent.
    await store.save_view("alice", "chart", "k", "V", {"c": 1})
    assert (await store.list_views("alice", "grid", "k"))[0]["payload"] == {"u": "a"}
    assert (await store.list_views("alice", "chart", "k"))[0]["payload"] == {"c": 1}


@pytest.mark.asyncio
async def test_app_scoped_keys_dont_collide(store: UserViewStore) -> None:
    """Same table name in two apps → distinct app-scoped keys → distinct rows."""
    await store.save_view("alice", "grid", "app1::F0101::main", "V", {"cols": [1]})
    await store.save_view("alice", "grid", "app2::F0101::main", "V", {"cols": [2]})
    assert (await store.list_views("alice", "grid", "app1::F0101::main"))[0]["payload"] == {"cols": [1]}
    assert (await store.list_views("alice", "grid", "app2::F0101::main"))[0]["payload"] == {"cols": [2]}


# --------------------------------------------------------------------------- #
# API layer — router + per-user scoping (principal dependency overridden)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path):
    """A minimal app mounting just the views router, with the store wired and the
    current principal stubbed to 'alice'. The schema is created in a startup hook
    so it runs on the TestClient's event loop (avoids cross-loop aiosqlite reuse)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from liberty.auth.dependencies import get_current_principal
    from liberty.auth.principal import Principal
    from liberty.web import views_router

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    pools = PoolRegistry()
    pools.register_engine("default", engine)
    st = UserViewStore(pools, "default")

    app = FastAPI()

    @app.on_event("startup")
    async def _boot() -> None:
        await st.create_schema()

    app.state.user_views = st
    app.include_router(views_router)
    app.dependency_overrides[get_current_principal] = lambda: Principal(id="1", username="alice")
    with TestClient(app) as c:   # triggers startup → create_schema on the app loop
        yield c


def test_api_put_list_delete(client) -> None:
    # Nothing saved yet → empty list.
    r = client.get("/api/views/grid", params={"key": "app::scr::g"})
    assert r.status_code == 200 and r.json()["views"] == []
    # Save two named views.
    r = client.put("/api/views/grid", json={"key": "app::scr::g", "name": "Wide", "payload": {"columns": ["A", "B"]}})
    assert r.status_code == 200 and r.json()["ok"] is True
    client.put("/api/views/grid", json={"key": "app::scr::g", "name": "Narrow", "payload": {"columns": ["A"]}})
    # List back.
    r = client.get("/api/views/grid", params={"key": "app::scr::g"})
    views = r.json()["views"]
    assert {v["name"] for v in views} == {"Wide", "Narrow"}
    # Delete one.
    r = client.delete("/api/views/grid", params={"key": "app::scr::g", "name": "Wide"})
    assert r.json()["deleted"] is True
    r = client.get("/api/views/grid", params={"key": "app::scr::g"})
    assert {v["name"] for v in r.json()["views"]} == {"Narrow"}


def test_api_unknown_kind_404(client) -> None:
    assert client.get("/api/views/bogus", params={"key": "k"}).status_code == 404
    assert client.put("/api/views/bogus", json={"key": "k", "name": "V", "payload": {}}).status_code == 404
