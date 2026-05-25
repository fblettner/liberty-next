"""Tests for :class:`liberty.jobs.PythonStepExecutor` — the ``python`` step
executor that imports an operator-named callable and runs it.

The tests use functions defined in this module as the import target (loaded
via ``importlib.import_module("tests.test_jobs_steps_python")``) so we don't
have to ship a fake plugin package.
"""

from __future__ import annotations

import asyncio

import pytest

from liberty.connectors.config import ConnectorsFile, PoolConfig, SqlConnectorConfig
from liberty.connectors.registry import ConnectorRegistry
from liberty.jobs import (
    ManualTrigger,
    PythonStepExecutor,
    RunContext,
    Step,
    StepFailed,
    StepResult,
    StepType,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def registry(tmp_path):
    cfg = ConnectorsFile(
        pools={"default": PoolConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")},
        connectors={"db": SqlConnectorConfig(type="sql", pool="default", queries=[])},
    )
    return ConnectorRegistry(cfg)


def _ctx() -> RunContext:
    return RunContext(
        run_id="py-run-1",
        job_id="py-job-1",
        trigger=ManualTrigger(triggered_by="tests"),
    )


def _step(*, name="py", callable: str, op_kwargs: dict | None = None) -> Step:
    return Step.model_validate({
        "type": StepType.PYTHON.value,
        "name": name,
        "callable": callable,
        "op_kwargs": op_kwargs or {},
    })


# --------------------------------------------------------------------------- #
# callables under test — imported via the executor by their dotted name
# --------------------------------------------------------------------------- #


def echo_kwargs(**kw):
    """Returns the merged kwargs (op_kwargs + injected). Used to verify the
    executor passes what we expect."""
    return kw


async def async_returns_int():
    """Async callable returning a row count."""
    await asyncio.sleep(0)  # actually awaits
    return 42


def sync_returns_step_result():
    return StepResult(rows_affected=7, extras={"note": "hi"})


def returns_dict():
    return {"users": 100, "roles": 5}


def returns_none():
    return None


def returns_bool():
    return True


def returns_unsupported():
    return "not a valid return"


def needs_connectors(*, connectors, x: int):
    """The executor should inject ``connectors`` when the param is named."""
    assert connectors is not None
    return x * 2


def needs_ctx_and_connectors(*, ctx, connectors, label: str):
    assert ctx.run_id == "py-run-1"
    assert ctx.trigger.triggered_by == "tests"
    return {"label": label, "got_registry": connectors is not None}


def positional_only_swallows(label="hello", **_):
    """Function that takes **kwargs — should receive both connectors and ctx."""
    return _


def raises_keyerror():
    raise KeyError("simulated failure")


def needs_settings(*, settings, label: str):
    """Used by the settings-injection tests below — only callable when the executor
    was constructed with a real Settings object."""
    return {"label": label, "got_settings": settings is not None}


# --------------------------------------------------------------------------- #
# resolve + dispatch
# --------------------------------------------------------------------------- #


_MOD = "tests.test_jobs_steps_python"


@pytest.mark.asyncio
async def test_async_callable_int_result(registry) -> None:
    """An async function returning an int → StepResult.rows_affected."""
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:async_returns_int"), _ctx(),
    )
    assert res.rows_affected == 42


@pytest.mark.asyncio
async def test_sync_callable_step_result(registry) -> None:
    """A sync function returning a StepResult is used verbatim — sync
    callables run via ``asyncio.to_thread`` so they don't stall the loop."""
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:sync_returns_step_result"), _ctx(),
    )
    assert res.rows_affected == 7
    assert res.extras == {"note": "hi"}


@pytest.mark.asyncio
async def test_dict_return_wraps_to_extras(registry) -> None:
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:returns_dict"), _ctx(),
    )
    assert res.rows_affected is None
    assert res.extras == {"users": 100, "roles": 5}


@pytest.mark.asyncio
async def test_none_return_is_empty_step_result(registry) -> None:
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:returns_none"), _ctx(),
    )
    assert res.rows_affected is None
    assert res.extras == {}


@pytest.mark.asyncio
async def test_bool_return_is_step_failed(registry) -> None:
    """Returning ``True`` would silently become rows_affected=1; force the
    operator to be explicit."""
    with pytest.raises(StepFailed) as exc:
        await PythonStepExecutor(registry).execute(
            _step(callable=f"{_MOD}:returns_bool"), _ctx(),
        )
    assert "bool" in str(exc.value)


@pytest.mark.asyncio
async def test_unsupported_return_is_step_failed(registry) -> None:
    with pytest.raises(StepFailed) as exc:
        await PythonStepExecutor(registry).execute(
            _step(callable=f"{_MOD}:returns_unsupported"), _ctx(),
        )
    assert "unsupported" in str(exc.value).lower() or "str" in str(exc.value)


# --------------------------------------------------------------------------- #
# kwargs forwarding + injection
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_op_kwargs_forwarded_verbatim(registry) -> None:
    """op_kwargs flow through, *plus* the executor injects connectors + ctx
    because echo_kwargs accepts ``**kw`` (the operator opted in to the
    "give me everything" convention)."""
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:echo_kwargs", op_kwargs={"a": 1, "b": "x"}),
        _ctx(),
    )
    extras = res.extras
    assert extras["a"] == 1 and extras["b"] == "x"
    assert "connectors" in extras and "ctx" in extras


@pytest.mark.asyncio
async def test_injects_connectors_when_named(registry) -> None:
    """A callable that declares ``connectors`` gets the registry injected."""
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:needs_connectors", op_kwargs={"x": 21}),
        _ctx(),
    )
    assert res.rows_affected == 42


@pytest.mark.asyncio
async def test_injects_ctx_and_connectors(registry) -> None:
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:needs_ctx_and_connectors", op_kwargs={"label": "hi"}),
        _ctx(),
    )
    assert res.extras == {"label": "hi", "got_registry": True}


@pytest.mark.asyncio
async def test_var_kwargs_receives_both_injections(registry) -> None:
    """A function with ``**kwargs`` gets both connectors and ctx — same as
    Airflow's op_kwargs convention where unknown extras flow through."""
    res = await PythonStepExecutor(registry).execute(
        _step(callable=f"{_MOD}:positional_only_swallows", op_kwargs={"label": "bye"}),
        _ctx(),
    )
    extras = res.extras
    assert "connectors" in extras
    assert "ctx" in extras
    # op_kwargs `label` is the positional default — not in `**_`.
    assert "label" not in extras


@pytest.mark.asyncio
async def test_op_kwargs_can_override_injection(registry) -> None:
    """If the operator passes ``connectors`` in op_kwargs, that wins —
    useful for tests that inject a stub registry. The executor uses
    ``setdefault`` to honour the override."""
    sentinel = object()
    res = await PythonStepExecutor(registry).execute(
        _step(
            callable=f"{_MOD}:needs_connectors",
            op_kwargs={"x": 5, "connectors": sentinel},  # type: ignore[dict-item]
        ),
        _ctx(),
    )
    # needs_connectors asserts connectors is not None — sentinel passes that
    # and the function does x*2.
    assert res.rows_affected == 10


# --------------------------------------------------------------------------- #
# resolution + failure paths
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_module_is_step_failed(registry) -> None:
    with pytest.raises(StepFailed) as exc:
        await PythonStepExecutor(registry).execute(
            _step(callable="no.such.module:fn"), _ctx(),
        )
    assert "no.such.module" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_function_is_step_failed(registry) -> None:
    with pytest.raises(StepFailed) as exc:
        await PythonStepExecutor(registry).execute(
            _step(callable=f"{_MOD}:no_such_function"), _ctx(),
        )
    assert "no_such_function" in str(exc.value)


@pytest.mark.asyncio
async def test_callable_exception_becomes_step_failed(registry) -> None:
    """A plain exception in the callable surfaces as StepFailed so the runner's
    retry policy applies (PHASE13 §3.3)."""
    with pytest.raises(StepFailed) as exc:
        await PythonStepExecutor(registry).execute(
            _step(callable=f"{_MOD}:raises_keyerror"), _ctx(),
        )
    assert "simulated failure" in str(exc.value)


@pytest.mark.asyncio
async def test_settings_injected_when_executor_built_with_one(registry, tmp_path) -> None:
    """A callable that declares ``settings`` gets the live Settings object — used by
    config-management steps like clone-app that operate on TOML paths under
    settings.<section>. Build the executor with settings=<...> and verify."""
    from liberty.config import AppSettings, AuthSettings, Settings
    settings = Settings(
        app=AppSettings(static_dir=""),
        auth=AuthSettings(backend="db", jwt_secret="x", pool="default"),
    )
    executor = PythonStepExecutor(registry, settings=settings)
    res = await executor.execute(
        _step(callable=f"{_MOD}:needs_settings", op_kwargs={"label": "hi"}),
        _ctx(),
    )
    assert res.extras == {"label": "hi", "got_settings": True}


@pytest.mark.asyncio
async def test_settings_not_injected_when_executor_built_without_one(registry) -> None:
    """The default executor wiring (no settings passed) still works — a callable that
    declares ``settings`` gets a clear TypeError, NOT a silent None (because injecting
    None would mask config-management steps that legitimately need a real Settings)."""
    # executor.settings is None by default; needs_settings expects a real one, so it
    # fails because the kwarg isn't provided — that surfaces as StepFailed from the
    # executor's exception wrapper.
    with pytest.raises(StepFailed):
        await PythonStepExecutor(registry).execute(
            _step(callable=f"{_MOD}:needs_settings", op_kwargs={"label": "hi"}),
            _ctx(),
        )


@pytest.mark.asyncio
async def test_wrong_step_type_is_step_failed(registry) -> None:
    # Hand-build a sql_query step but feed it to the python executor — the
    # runner would never dispatch this way, but the guard documents the
    # contract.
    bad = Step.model_validate({
        "type": StepType.SQL_QUERY.value, "name": "x",
        "connector": "db", "query": "answer",
    })
    with pytest.raises(StepFailed):
        await PythonStepExecutor(registry).execute(bad, _ctx())
