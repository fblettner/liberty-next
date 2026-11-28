"""Kept answers (:mod:`liberty.kept`): the store, the page call's ``kept`` modes, the job step."""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from liberty.connectors.config import PoolConfig
from liberty.connectors.db import PoolRegistry
from liberty.kept import KeptStore, answer_key, j_keep, scope_of
from tests.test_web_plugins import CALLABLE, _h, env  # noqa: F401, I001 — `env` is the application fixture; loads the application first
from liberty.plugins.invoke import invoke_callable

REF = "app.review:j_review"
_CALLS: list[dict] = []


async def j_sample(*, connectors, apps_id: int, lang: str = "en") -> dict:
    """What a page function looks like, for the job step."""
    _CALLS.append({"apps_id": apps_id, "lang": lang})
    return {"apps_id": apps_id, "lang": lang, "calls": len(_CALLS), "connectors": connectors is not None}


@pytest.fixture
async def store(tmp_path):
    pools = PoolRegistry({"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'kept.db'}")})
    kept = KeptStore(pools, "default")
    await kept.create_schema()
    yield kept
    await pools.dispose()


def test_key_names_the_function_its_parameters_and_the_scope() -> None:
    assert answer_key(REF, {"a": 1, "b": 2}) == answer_key(REF, {"b": 2, "a": 1})
    assert answer_key(REF, {"a": 1}) != answer_key(REF, {"a": 2})
    assert answer_key(REF, {"a": 1}) != answer_key("app.review:j_other", {"a": 1})
    assert answer_key(REF, {"a": 1}) != answer_key(REF, {"a": 1}, [["CO", ["00001"]]])
    assert answer_key(REF, {"a": 1}, []) == answer_key(REF, {"a": 1})
    assert answer_key(REF, {"a": 1}) == answer_key(REF, {"a": "1"}) == answer_key(REF, {"a": 1.0}) == answer_key(REF, {"a": 1, "b": None})


def test_scope_of_a_caller() -> None:
    group = ("dd", "CO", ("00001",))
    assert scope_of(None) == []
    assert scope_of(types.SimpleNamespace(is_superuser=True, scope=(group,))) == []
    assert scope_of(types.SimpleNamespace(is_superuser=False, scope=())) == []
    assert scope_of(types.SimpleNamespace(is_superuser=False, scope=(group,))) == [list(group)]


async def test_store_keeps_replaces_and_forgets(store: KeptStore) -> None:
    assert await store.get(REF, {"apps_id": 1}) is None
    said = await store.put(REF, {"apps_id": 1}, {"users": 3}, by="job", duration_ms=1200)
    hit = await store.get(REF, {"apps_id": 1})
    assert hit == {"answer": {"users": 3}, "at": said["at"], "by": "job", "ms": 1200}
    assert hit["at"].endswith("+00:00")
    await store.put(REF, {"apps_id": 1}, {"users": 4}, by="ann")
    assert (await store.get(REF, {"apps_id": 1}))["answer"] == {"users": 4}
    assert await store.get(REF, {"apps_id": 2}) is None
    assert await store.forget(REF) == 1
    assert await store.get(REF, {"apps_id": 1}) is None


async def test_an_answer_is_never_served_to_another_scope(store: KeptStore) -> None:
    scope = [["dd", "CO", ["00001"]]]
    await store.put(REF, {"apps_id": 1}, {"users": 100})                      # the unrestricted answer (a job)
    assert await store.get(REF, {"apps_id": 1}, scope) is None
    await store.put(REF, {"apps_id": 1}, {"users": 7}, scope=scope)
    assert (await store.get(REF, {"apps_id": 1}, scope))["answer"] == {"users": 7}
    assert (await store.get(REF, {"apps_id": 1}))["answer"] == {"users": 100}
    assert await store.get(REF, {"apps_id": 1}, [["dd", "CO", ["00002"]]]) is None


async def test_job_step_runs_the_function_and_keeps_its_answer(store: KeptStore) -> None:
    _CALLS.clear()
    state = types.SimpleNamespace(kept=store)
    # as a job hands it: its own parameters next to the step's — only what the function declares reaches it
    result = await invoke_callable(
        "liberty.kept:j_keep", {"function": "tests.test_kept:j_sample", "apps_id": "3", "source_connector": "jde", "langs": "en, fr"},
        injections=(("connectors", object()), ("app_state", state)))
    assert result.extras["answers"] == 2 and [k["params"] for k in result.extras["kept"]] == [{"apps_id": "3", "lang": "en"}, {"apps_id": "3", "lang": "fr"}]
    assert _CALLS == [{"apps_id": 3, "lang": "en"}, {"apps_id": 3, "lang": "fr"}]                # coerced as the page's call is
    hit = await store.get("tests.test_kept:j_sample", {"apps_id": 3, "lang": "fr"})             # what the page sends: a number
    assert hit["answer"] == {"apps_id": 3, "lang": "fr", "calls": 2, "connectors": True} and hit["by"] == "job"
    assert await store.get("tests.test_kept:j_sample", {"apps_id": 3}) is None


async def j_where(*, connectors: object = None, **_: object) -> dict:
    from liberty.connectors.api import current_pool
    if current_pool.get() == "jde_down":
        raise RuntimeError("the database does not answer")
    return {"read": current_pool.get() or "main"}


async def test_job_step_keeps_one_answer_per_environment(store: KeptStore) -> None:
    """A connector on several databases: the step keeps an answer for each, the main one as a page asks for it."""
    from liberty.connectors.api import current_pool

    conn = types.SimpleNamespace(pool_name="jde_main", allowed_pools=frozenset({"jde_main", "jde_other", "jde_down"}))
    registry = types.SimpleNamespace(get=lambda name: conn)
    state = types.SimpleNamespace(kept=store)
    out = await j_keep(connectors=registry, app_state=state, function="tests.test_kept:j_where", every_pool_of="jdedwards")
    assert out["answers"] == 2 and [k.get("pool") for k in out["kept"]] == [None, "jde_other"] and out["not_kept"][0].startswith("jde_down")
    assert (await store.get("tests.test_kept:j_where", {}))["answer"] == {"read": "main"}          # nothing picked on the page
    for pool, read in (("jde_other", "jde_other"), ("jde_down", None)):
        token = current_pool.set(pool)
        try:
            hit = await store.get("tests.test_kept:j_where", {})
            assert (hit and hit["answer"]["read"]) == read
        finally:
            current_pool.reset(token)
    # a run that has its own environment keeps that one; the main database by its name is kept as the page asks for it
    await store.forget("tests.test_kept:j_where")
    for pool, kept_as in (("jde_other", "jde_other"), ("jde_main", None)):
        token = current_pool.set(pool)
        try:
            one = await j_keep(connectors=registry, app_state=state, function="tests.test_kept:j_where", every_pool_of="jdedwards")
        finally:
            current_pool.reset(token)
        assert one["answers"] == 1 and one["kept"][0].get("pool") == kept_as
    assert (await store.get("tests.test_kept:j_where", {}))["answer"] == {"read": "main"}
    with pytest.raises(RuntimeError, match="does not answer"):                                       # one environment, and it fails: the step fails
        token = current_pool.set("jde_down")
        try:
            await j_keep(connectors=registry, app_state=state, function="tests.test_kept:j_where", every_pool_of="jdedwards")
        finally:
            current_pool.reset(token)


async def test_job_step_says_when_there_is_no_store() -> None:
    with pytest.raises(RuntimeError, match="store is not available"):
        await j_keep(connectors=object(), app_state=types.SimpleNamespace(), function="tests.test_kept:j_sample", apps_id=1)


def _run(client: TestClient, user: str, **more: object):
    return client.post("/api/plugins/run", headers=_h(client, user), json={"callable": CALLABLE, "params": {"role_id": "7"}, **more})


def test_page_call_uses_what_is_kept_and_reads_again(env) -> None:  # noqa: F811
    with TestClient(env) as client:
        plain = _run(client, "merger").json()
        assert "kept" not in plain                                              # a plain call keeps nothing
        first = _run(client, "merger", kept="use").json()
        assert first["kept"]["fresh"] is True and first["kept"]["by"] == "merger"
        again = _run(client, "merger", kept="use").json()
        assert again["kept"]["fresh"] is False and again["kept"]["at"] == first["kept"]["at"]
        assert again["extras"] == first["extras"] == plain["extras"]
        anew = _run(client, "merger", kept="refresh").json()
        assert anew["kept"]["fresh"] is True and anew["kept"]["at"] > first["kept"]["at"]
        assert _run(client, "admin", kept="use").json()["kept"]["fresh"] is False   # no scope either: the same answer


def test_page_call_checks_the_permission_before_the_store(env) -> None:  # noqa: F811
    with TestClient(env) as client:
        assert _run(client, "merger", kept="use").status_code == 200
        assert _run(client, "reader", kept="use").status_code == 403           # kept, but not for who may not run it
        unknown = client.post("/api/plugins/run", headers=_h(client, "merger"), json={"callable": "plg.merge:j_no", "kept": "use"})
        assert unknown.status_code == 404
