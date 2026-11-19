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
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from liberty.auth.principal import Principal, current_principal


class ToolPermissionError(PermissionError):
    """The signed-in user may not do what the tool was asked: the message goes back to the
    model, which tells the user — it must not look for another way round."""


# Where the assistant runs for this call: "chat" (the Liberty chat: a tool that saves a record
# proposes it, the user applies the card) or "mcp" (an external client, which asks its own user
# before a tool that is not read-only). Set by the MCP endpoint; the chat is the default.
current_channel: ContextVar[str] = ContextVar("liberty_ai_channel", default="chat")


HIDDEN = "(hidden: sensitive)"          # what stands for a sensitive value in what the assistant is given


def hides_sensitive(state: Any) -> bool:
    """Whether this installation keeps the data it flagged sensitive out of what the assistant is
    given (``[ai] hide_sensitive``) — read on the live state, so a change of the setting applies
    at the next call."""
    return bool(getattr(getattr(getattr(state, "settings", None), "ai", None), "hide_sensitive", False))


def caller() -> Principal | None:
    """The user the assistant is working for; None outside a request (nothing restricted)."""
    return current_principal.get()


def may(permission: str) -> bool:
    """Whether the caller holds *permission* — always true outside a request."""
    who = current_principal.get()
    return who is None or who.has_permission(permission)


def require(permission: str, what: str) -> None:
    """Refuse when the caller lacks *permission*; *what* names the thing in the message."""
    if not may(permission):
        raise ToolPermissionError(
            f"permission denied: the signed-in user has no access to {what} (missing {permission}). "
            "Tell the user; do not try another query or tool to get the same data."
        )


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
    superuser_only: bool = False        # configuration / operations tools: offered to and run for superusers only
    permission: str | None = None       # a permission the caller must hold to be offered the tool and to run it
    chat_only: bool = False             # its result is a card of the Liberty chat (a proposal to apply): not offered over MCP
    read_only: bool = True              # False for a tool that saves something (told to an MCP client as a hint)

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


def schema_of(fn: Callable[..., Any], *, exclude: typing.Iterable[str] = ()) -> tuple[str, dict[str, Any]]:
    """``(description, input_schema)`` of a function: the docstring's text, and a JSON Schema
    object from its parameters — type from the hint, description from the ``Args:`` block,
    required when there is no default. *exclude* names parameters the model never supplies
    (what the framework injects into a plugin callable)."""
    skip = set(exclude)
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # forward refs that don't resolve — degrade to str
        hints = {}
    doc_desc, doc_params = _parse_docstring(fn.__doc__)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) or pname in skip:
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
    return doc_desc, input_schema


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    summary_keys: tuple[str, ...] = (),
    superuser_only: bool = False,
    chat_only: bool = False,
    read_only: bool = True,
    permission: str | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Turn a function into a :class:`Tool` (usable as ``@tool`` or ``@tool(...)``)."""

    def build(fn: Callable[..., Any]) -> Tool:
        doc_desc, input_schema = schema_of(fn)
        return Tool(
            name=name or fn.__name__,
            description=description or doc_desc or fn.__name__,
            input_schema=input_schema,
            func=fn,
            is_async=inspect.iscoroutinefunction(fn),
            summary_keys=summary_keys,
            superuser_only=superuser_only,
            chat_only=chat_only,
            read_only=read_only,
            permission=permission,
        )

    return build(func) if func is not None else build


@dataclass(slots=True)
class ToolRegistry:
    """An ordered, name-keyed set of tools. Order is stable → prompt-cache friendly.

    *provider* adds tools read at call time — what the applications ship in ``ai.toml``, seen
    after a reload without rebuilding the assistant. They come after the built-in ones, and a
    built-in name always wins."""

    _tools: dict[str, Tool] = field(default_factory=dict)
    provider: Callable[[], list[Tool]] | None = None

    def _all(self) -> dict[str, Tool]:
        if self.provider is None:
            return self._tools
        out = dict(self._tools)
        for t in self.provider():
            out.setdefault(t.name, t)
        return out

    def add(self, *tools: Tool) -> ToolRegistry:
        for t in tools:
            self._tools[t.name] = t
        return self

    def __len__(self) -> int:
        return len(self._all())

    def __contains__(self, name: object) -> bool:
        return name in self._all()

    def names(self) -> list[str]:
        return list(self._all())

    def get(self, name: str) -> Tool | None:
        return self._all().get(name)

    def offered(self, principal: Principal | None = None) -> list[Tool]:
        """The tools *principal* is offered: all of them for a superuser (or outside a request),
        the others without the superuser-only ones. Two stable lists, so the prompt cache holds."""
        tools = list(self._all().values())
        if principal is None or principal.is_superuser:
            return tools
        return [t for t in tools if not t.superuser_only and (t.permission is None or principal.has_permission(t.permission))]

    def definitions(self, principal: Principal | None = None) -> list[dict[str, Any]]:
        return [t.definition() for t in self.offered(principal)]

    async def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run tool *name* with *arguments*; return (content_string, is_error)."""
        t = self.get(name)
        if t is None:
            return json.dumps({"error": f"unknown tool: {name}"}), True
        who = current_principal.get()
        if t.superuser_only and who is not None and not who.is_superuser:
            return json.dumps({"error": f"permission denied: {name} is for administrators"}), True
        if t.permission is not None and who is not None and not who.has_permission(t.permission):
            return json.dumps({"error": f"permission denied: the signed-in user may not use {name} (missing {t.permission}). Tell the user."}), True
        try:
            result = await t.run(arguments)
        except Exception as exc:  # surface to the model so it can adjust
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
        if isinstance(result, str):
            return result, False
        return json.dumps(result, default=str), False
