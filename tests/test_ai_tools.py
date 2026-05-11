from __future__ import annotations

from typing import Any

import pytest

from liberty.ai.tools import Tool, ToolRegistry, tool


def test_schema_from_type_hints() -> None:
    @tool
    def search(query: str, limit: int = 10, fuzzy: bool = False, extra: dict | None = None) -> dict:
        """Search the catalog.

        Args:
            query: the search string.
            limit: max rows to return.
        """

    assert isinstance(search, Tool)
    assert search.name == "search"
    assert search.description == "Search the catalog."
    schema = search.input_schema
    assert schema["type"] == "object"
    assert schema["properties"]["query"] == {"type": "string", "description": "the search string."}
    assert schema["properties"]["limit"] == {"type": "integer", "description": "max rows to return."}
    assert schema["properties"]["fuzzy"] == {"type": "boolean"}
    assert schema["properties"]["extra"] == {"type": "object"}  # dict | None → object, optional
    assert schema["required"] == ["query"]  # only the no-default param


def test_optional_via_union_none() -> None:
    @tool
    def f(a: str, b: int | None = None) -> str:
        return f"{a}{b}"

    assert f.input_schema["properties"]["b"] == {"type": "integer"}
    assert f.input_schema["required"] == ["a"]


def test_list_annotation() -> None:
    @tool
    def f(items: list[str]) -> int:
        return len(items)

    assert f.input_schema["properties"]["items"] == {"type": "array", "items": {"type": "string"}}


def test_explicit_name_and_description() -> None:
    @tool(name="lookup", description="custom desc", summary_keys=("code",))
    def x(code: str) -> str:
        """ignored docstring"""
        return code

    assert x.name == "lookup"
    assert x.description == "custom desc"
    assert x.summarize({"code": "9906", "noise": "z"}) == "code=9906"


def test_definition_shape() -> None:
    @tool
    def t(a: str) -> str:
        """desc"""
        return a

    assert t.definition() == {"name": "t", "description": "desc", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}}


@pytest.mark.asyncio
async def test_run_sync_and_async() -> None:
    @tool
    def s(x: int) -> int:
        return x + 1

    @tool
    async def a(x: int) -> int:
        return x + 2

    assert await s.run({"x": 1}) == 2
    assert a.is_async and await a.run({"x": 1}) == 3


@pytest.mark.asyncio
async def test_registry_execute() -> None:
    @tool
    def ok(n: int) -> dict:
        return {"doubled": n * 2}

    @tool
    def stringy() -> str:
        return "plain text"

    @tool
    def boom() -> str:
        raise ValueError("kaboom")

    reg = ToolRegistry().add(ok, stringy, boom)
    assert reg.names() == ["ok", "stringy", "boom"]
    assert "ok" in reg and len(reg) == 3

    content, is_error = await reg.execute("ok", {"n": 21})
    assert not is_error and content == '{"doubled": 42}'

    content, is_error = await reg.execute("stringy", {})
    assert not is_error and content == "plain text"

    content, is_error = await reg.execute("boom", {})
    assert is_error and "ValueError" in content and "kaboom" in content

    content, is_error = await reg.execute("nope", {})
    assert is_error and "unknown tool" in content


def test_summarize_defaults_to_present_inputs() -> None:
    @tool
    def t(a: str = "", b: str = "") -> str:
        return a + b

    assert t.summarize({"a": "x"}) == "a=x"
    assert t.summarize({}) == "t"
