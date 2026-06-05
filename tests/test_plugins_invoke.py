"""The shared plugin invoker (:mod:`liberty.plugins.invoke`) — the resolve / coerce+inject / call /
normalise core used by BOTH nomaflow python steps and ``call_plugin`` screen actions. The job-step
behaviour is covered end-to-end in test_jobs_steps_python.py; this pins the extracted core directly
so the action surface can rely on it too."""

from __future__ import annotations

import pytest

from liberty.jobs.steps.base import StepResult
from liberty.plugins.invoke import (
    PluginInvocationError,
    build_kwargs,
    call_target,
    invoke_callable,
    normalise_result,
    resolve_callable,
)


# ── resolve_callable ──────────────────────────────────────────────────────────────────────────
def test_resolve_callable_imports_module_function() -> None:
    fn = resolve_callable("liberty.plugins.invoke:normalise_result")
    assert fn is normalise_result


@pytest.mark.parametrize("ref", ["nodots", "module:", ":function", ""])
def test_resolve_callable_rejects_malformed_ref(ref: str) -> None:
    with pytest.raises(PluginInvocationError, match="module:function"):
        resolve_callable(ref)


def test_resolve_callable_missing_module() -> None:
    with pytest.raises(PluginInvocationError, match="cannot import module"):
        resolve_callable("liberty.no_such_module:foo")


def test_resolve_callable_missing_attribute() -> None:
    with pytest.raises(PluginInvocationError, match="has no attribute"):
        resolve_callable("liberty.plugins.invoke:not_a_real_function")


# ── build_kwargs (coercion + injection) ───────────────────────────────────────────────────────
def test_build_kwargs_coerces_against_annotations() -> None:
    def fn(*, apps_id: int, flag: bool) -> None: ...
    kwargs = build_kwargs(fn, {"apps_id": "7", "flag": "true"}, injections=())
    assert kwargs == {"apps_id": 7, "flag": True}


def test_build_kwargs_coercion_failure_raises() -> None:
    def fn(*, apps_id: int) -> None: ...
    with pytest.raises(PluginInvocationError, match="cannot be coerced"):
        build_kwargs(fn, {"apps_id": "not-a-number"}, injections=())


def test_build_kwargs_injects_only_declared_names() -> None:
    def fn(*, connectors, label: str) -> None: ...
    kwargs = build_kwargs(fn, {"label": "x"}, injections=[("connectors", "REG"), ("ctx", "CTX")])
    assert kwargs == {"label": "x", "connectors": "REG"}   # ctx not declared → not injected


def test_build_kwargs_var_kwargs_receives_all_injections() -> None:
    def fn(**kw) -> None: ...
    kwargs = build_kwargs(fn, {}, injections=[("connectors", "REG"), ("ctx", "CTX")])
    assert kwargs == {"connectors": "REG", "ctx": "CTX"}


def test_build_kwargs_skips_none_injection() -> None:
    def fn(*, settings=None) -> None: ...
    kwargs = build_kwargs(fn, {}, injections=[("settings", None)])
    assert kwargs == {}   # None is never injected (don't stub over a real-expected param)


def test_build_kwargs_op_kwargs_win_over_injection() -> None:
    def fn(*, connectors) -> None: ...
    kwargs = build_kwargs(fn, {"connectors": "OPERATOR"}, injections=[("connectors", "FRAMEWORK")])
    assert kwargs == {"connectors": "OPERATOR"}


# ── normalise_result ──────────────────────────────────────────────────────────────────────────
def test_normalise_none_is_empty() -> None:
    assert normalise_result(None) == StepResult()


def test_normalise_int_is_rows_affected() -> None:
    assert normalise_result(42).rows_affected == 42


def test_normalise_dict_is_extras() -> None:
    assert normalise_result({"roles": 3}).extras == {"roles": 3}


def test_normalise_step_result_verbatim() -> None:
    sr = StepResult(rows_affected=5, extras={"a": 1})
    assert normalise_result(sr) is sr


def test_normalise_bool_rejected() -> None:
    with pytest.raises(PluginInvocationError, match="bool"):
        normalise_result(True)


def test_normalise_unsupported_rejected() -> None:
    with pytest.raises(PluginInvocationError, match="unsupported type str"):
        normalise_result("nope")


# ── call_target + invoke_callable (end-to-end) ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_call_target_async_and_sync() -> None:
    async def afn(*, x: int) -> int:
        return x + 1
    def sfn(*, x: int) -> int:
        return x * 2
    assert await call_target(afn, {"x": 1}) == 2
    assert await call_target(sfn, {"x": 3}) == 6


@pytest.mark.asyncio
async def test_invoke_callable_full_path() -> None:
    # Resolve a real callable, coerce a string arg, inject connectors, normalise the dict return.
    async def fake(*, connectors, apps_id: int) -> dict:
        return {"apps_id": apps_id, "got_reg": connectors == "REG"}
    import sys
    sys.modules[__name__]  # this module is importable; attach the fn for resolve
    setattr(sys.modules[__name__], "_fake_target", fake)
    res = await invoke_callable(
        f"{__name__}:_fake_target", {"apps_id": "9"}, injections=[("connectors", "REG")],
    )
    assert res.extras == {"apps_id": 9, "got_reg": True}
