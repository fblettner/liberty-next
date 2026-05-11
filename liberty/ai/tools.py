"""Local tools for the assistant — decorated Python functions, schema from type hints.

A tool is an ordinary (sync or async) function. The :func:`tool` decorator
inspects its signature + type hints to build the Anthropic ``input_schema`` (a
JSON Schema object) and reads the docstring for the tool description and, in a
Google-style ``Args:`` block, per-parameter descriptions. Parameters with a
default are optional; everything else is ``required``.

The runtime executes whichever tool the model picks and feeds the (JSON-encoded)
return value back as a ``tool_result``. This mirrors nomaubl's
``AiAssistant.executeLocalTool`` switch, but the dispatch table is built from
decorated functions instead of a hand-maintained ``case`` ladder.
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

_JSON_TYPES: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    dict: {"type": "object"},
    list: {"type": "array"},
}


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    """Best-effort JSON-Schema fragment for one parameter's type hint."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        # X | None  → schema for X (None just means the param is optional).
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _schema_for_annotation(args[0]) if len(args) == 1 else {"type": "string"}
    if origin in (list, tuple, set):
        item_args = typing.get_args(annotation)
        return {"type": "array", **({"items": _schema_for_annotation(item_args[0])} if item_args else {})}
    if origin is dict:
        return {"type": "object"}
    if annotation in _JSON_TYPES:
        return dict(_JSON_TYPES[annotation])
    if isinstance(annotation, type) and issubclass(annotation, (str, int, float, bool)):
        return dict(_JSON_TYPES[annotation])
    return {"type": "string"}


_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", re.IGNORECASE)
_ARG_LINE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+?)\s*$")


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a docstring into (description, {param: description}) — Google style."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    desc: list[str] = []
    params: dict[str, str] = {}
    in_args = False
    for line in lines:
        if _ARGS_HEADER.match(line):
            in_args = True
            continue
        if in_args:
            if not line.strip():
                continue
            m = _ARG_LINE.match(line)
            if m:
                params[m.group(1)] = m.group(2)
            # a non-matching, non-blank line ends the Args block
            elif not line.startswith((" ", "\t")):
                in_args = False
                desc.append(line)
        else:
            desc.append(line)
    return "\n".join(desc).strip(), params


@dataclass(slots=True)
class Tool:
    """A callable tool plus the metadata the Messages API needs to offer it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    is_async: bool = False
    summary_keys: tuple[str, ...] = ()  # which input keys to surface in the UI indicator

    def definition(self) -> dict[str, Any]:
        """The ``tools[]`` entry for the Messages API request."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    async def run(self, arguments: dict[str, Any]) -> Any:
        if self.is_async:
            return await self.func(**arguments)
        return self.func(**arguments)

    def summarize(self, arguments: dict[str, Any]) -> str:
        keys = self.summary_keys or tuple(arguments)
        parts = [f"{k}={arguments[k]}" for k in keys if k in arguments and arguments[k] not in (None, "")]
        return " ".join(parts) if parts else self.name


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    summary_keys: tuple[str, ...] = (),
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Turn a function into a :class:`Tool` (usable as ``@tool`` or ``@tool(...)``)."""

    def build(fn: Callable[..., Any]) -> Tool:
        sig = inspect.signature(fn)
        try:
            hints = typing.get_type_hints(fn)
        except Exception:  # forward refs that don't resolve — degrade to str
            hints = {}
        doc_desc, doc_params = _parse_docstring(fn.__doc__)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            schema = _schema_for_annotation(hints.get(pname, param.annotation))
            if pname in doc_params:
                schema = {**schema, "description": doc_params[pname]}
            properties[pname] = schema
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        input_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required
        return Tool(
            name=name or fn.__name__,
            description=description or doc_desc or fn.__name__,
            input_schema=input_schema,
            func=fn,
            is_async=inspect.iscoroutinefunction(fn),
            summary_keys=summary_keys,
        )

    return build(func) if func is not None else build


@dataclass(slots=True)
class ToolRegistry:
    """An ordered, name-keyed set of tools. Order is stable → prompt-cache friendly."""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *tools: Tool) -> ToolRegistry:
        for t in tools:
            self._tools[t.name] = t
        return self

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run tool *name* with *arguments*; return (content_string, is_error)."""
        t = self.get(name)
        if t is None:
            return json.dumps({"error": f"unknown tool: {name}"}), True
        try:
            result = await t.run(arguments)
        except Exception as exc:  # surface to the model so it can adjust
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
        if isinstance(result, str):
            return result, False
        return json.dumps(result, default=str), False
