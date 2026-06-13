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
async def test_put_get_roundtrip(store: UserViewStore) -> None:
    assert await store.get("alice", "grid", "app::scr::g") is None
    fmt = {"columns": ["A", "B"], "sort": [{"id": "A", "desc": False}], "group_by": []}
    await store.put("alice", "grid", "app::scr::g", fmt)
    assert await store.get("alice", "grid", "app::scr::g") == fmt


@pytest.mark.asyncio
async def test_put_upserts(store: UserViewStore) -> None:
    await store.put("alice", "grid", "k", {"columns": ["A"]})
    await store.put("alice", "grid", "k", {"columns": ["A", "B", "C"]})
    got = await store.get("alice", "grid", "k")
    assert got == {"columns": ["A", "B", "C"]}


@pytest.mark.asyncio
async def test_delete_then_gone(store: UserViewStore) -> None:
    await store.put("alice", "grid", "k", {"x": 1})
    assert await store.delete("alice", "grid", "k") is True
    assert await store.get("alice", "grid", "k") is None
    assert await store.delete("alice", "grid", "k") is False  # already gone


@pytest.mark.asyncio
async def test_isolation_by_user_kind_and_key(store: UserViewStore) -> None:
    # Same key, different users → independent (the cross-app-collision guard's twin:
    # the key is app-scoped AND rows are per-user).
    await store.put("alice", "grid", "k", {"u": "a"})
    await store.put("bob", "grid", "k", {"u": "b"})
    assert await store.get("alice", "grid", "k") == {"u": "a"}
    assert await store.get("bob", "grid", "k") == {"u": "b"}
    # Same user+key, different kind → independent.
    await store.put("alice", "chart", "k", {"c": 1})
    assert await store.get("alice", "grid", "k") == {"u": "a"}
    assert await store.get("alice", "chart", "k") == {"c": 1}


@pytest.mark.asyncio
async def test_app_scoped_keys_dont_collide(store: UserViewStore) -> None:
    """Same table name in two apps → distinct app-scoped keys → distinct rows."""
    await store.put("alice", "grid", "app1::F0101::main", {"cols": [1]})
    await store.put("alice", "grid", "app2::F0101::main", {"cols": [2]})
    assert await store.get("alice", "grid", "app1::F0101::main") == {"cols": [1]}
    assert await store.get("alice", "grid", "app2::F0101::main") == {"cols": [2]}


@pytest.mark.asyncio
async def test_list_filters_by_user_and_kind(store: UserViewStore) -> None:
    await store.put("alice", "grid", "g1", {"a": 1})
    await store.put("alice", "grid", "g2", {"a": 2})
    await store.put("alice", "chart", "c1", {"a": 3})
    await store.put("bob", "grid", "g1", {"a": 9})

    all_alice = await store.list("alice")
    assert {(v["kind"], v["view_key"]) for v in all_alice} == {("grid", "g1"), ("grid", "g2"), ("chart", "c1")}
    grids = await store.list("alice", kind="grid")
    assert {v["view_key"] for v in grids} == {"g1", "g2"}
    assert all("updated_at" in v and v["payload"] for v in grids)


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


def test_api_put_get_delete(client) -> None:
    # Nothing saved yet → payload null.
    r = client.get("/api/views/grid", params={"key": "app::scr::g"})
    assert r.status_code == 200 and r.json()["payload"] is None
    # Save.
    r = client.put("/api/views/grid", json={"key": "app::scr::g", "payload": {"columns": ["A", "B"]}})
    assert r.status_code == 200 and r.json()["ok"] is True
    # Read back.
    r = client.get("/api/views/grid", params={"key": "app::scr::g"})
    assert r.json()["payload"] == {"columns": ["A", "B"]}
    # List.
    r = client.get("/api/views", params={"kind": "grid"})
    assert [v["view_key"] for v in r.json()["views"]] == ["app::scr::g"]
    # Delete → reset.
    r = client.delete("/api/views/grid", params={"key": "app::scr::g"})
    assert r.json()["deleted"] is True
    assert client.get("/api/views/grid", params={"key": "app::scr::g"}).json()["payload"] is None


def test_api_unknown_kind_404(client) -> None:
    assert client.get("/api/views/bogus", params={"key": "k"}).status_code == 404
    assert client.put("/api/views/bogus", json={"key": "k", "payload": {}}).status_code == 404
