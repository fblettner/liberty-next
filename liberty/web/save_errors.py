"""What a refused configuration save answers: which object, which field, what is wrong.

A builder sends a whole section; when a model refuses it, Pydantic's own text is
``1 validation error for ScreensFile … screens.demo.orders.columns.3.dd … [type=missing …`` — a
consultant cannot place it. :func:`config_invalid` turns the same error into one line per problem::

    demo › Screen "Orders" › Column "ABAN8" › Dictionary item: is required

and the answer carries, beside that text in ``detail``, the list ``errors`` — each with the ``path``
into the data that was sent, so the builder opens the object and names it by its label. The words
come from the data itself (a record's label, a column's name) and from the models' field titles.

The route raises what :func:`config_invalid` returns; :func:`install` registers the handler that
adds ``errors`` to the body. Without the handler the exception is a plain 422 with the same text.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from liberty.field_labels import title_of

#: How many problems an answer lists; the text shows the first few and counts the rest.
MAX_PROBLEMS = 20
_TEXT_LINES = 5

# What a record of each model is called. A class that is not here gets no noun: its label, name or
# key stands alone.
_NOUNS: dict[str, str] = {
    "Screen": "Screen",
    "ColumnHint": "Column",
    "ColumnGroup": "Column group",
    "FormTab": "Tab", "NestedFormTab": "Tab", "NestedTableTab": "Tab",
    "PromptField": "Prompt field",
    "ChartConfig": "Chart",
    "Dashboard": "Dashboard",
    "DashboardFilter": "Filter",
    "SharedAction": "Service",
    "ActionParam": "Input",
    "AppMenu": "Menu",
    "MenuItem": "Menu entry",
    "SqlConnectorConfig": "Connector", "ApiConnectorConfig": "Connector",
    "QueryDef": "Query",
    "TableDef": "Table",
    "EndpointDef": "Endpoint",
    "ParamDef": "Parameter",
    "PoolConfig": "Pool",
    "DictionaryEntry": "Dictionary entry",
    "EnumDef": "List of values",
    "LookupDef": "Lookup",
    "SequenceDef": "Sequence",
    "ExplorerModel": "Model",
    "ExplorerEntity": "Entity",
    "ExplorerRelation": "Relation",
    "NotificationRule": "Notification rule",
    "Job": "Job",
    "Step": "Step",
    "UserParam": "Parameter",
    "JobPreset": "Preset",
    "Playbook": "Playbook",
    "AiTool": "Tool",
    "PromotionTarget": "Target",
}

# Pydantic's error types said in plain words. ``{…}`` takes a value of the error's context.
_MESSAGES: dict[str, str] = {
    "missing": "is required",
    "extra_forbidden": "is not a setting that exists here",
    "string_type": "must be text",
    "int_type": "must be a whole number",
    "int_parsing": "must be a whole number",
    "int_from_float": "must be a whole number",
    "float_type": "must be a number",
    "float_parsing": "must be a number",
    "bool_type": "must be yes or no",
    "bool_parsing": "must be yes or no",
    "list_type": "must be a list",
    "dict_type": "is not in the expected shape",
    "model_type": "is not in the expected shape",
    "model_attributes_type": "is not in the expected shape",
    "literal_error": "must be one of: {expected}",
    "enum": "must be one of: {expected}",
    "string_pattern_mismatch": "has characters that are not allowed here",
    "string_too_short": "must not be empty",
    "string_too_long": "is too long (at most {max_length} characters)",
    "too_short": "needs at least {min_length}",
    "too_long": "takes at most {max_length}",
    "greater_than": "must be greater than {gt}",
    "greater_than_equal": "must be {ge} or more",
    "less_than": "must be less than {lt}",
    "less_than_equal": "must be {le} or less",
    "union_tag_not_found": "has no type",
    "union_tag_invalid": "has a type that does not exist ({tag}); expected one of: {expected_tags}",
    "url_parsing": "is not a valid address",
    "date_parsing": "is not a valid date",
    "datetime_parsing": "is not a valid date and time",
    "time_parsing": "is not a valid time",
}
# A branch of a choice between shapes that simply is not the shape that was sent.
_SHAPE_CODES = frozenset({"model_type", "dict_type", "list_type", "string_type", "int_type", "float_type",
                          "bool_type", "model_attributes_type", "tuple_type", "set_type"})


@dataclass
class Problem:
    """One thing wrong in a section that was sent."""

    path: list[str | int]
    where: str
    message: str
    code: str = ""
    crumbs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "where": self.where, "message": self.message, "code": self.code}

    @property
    def text(self) -> str:
        return f"{self.where}: {self.message}" if self.where else self.message


class ConfigInvalid(HTTPException):
    """A 422 whose ``detail`` is the readable text and which carries the problems one by one."""

    def __init__(self, section: str, problems: list[Problem], text: str) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=text)
        self.section = section
        self.problems = problems


def install(app: FastAPI) -> None:
    """Register the handler that answers ``{detail, section, errors}`` for a :class:`ConfigInvalid`."""

    @app.exception_handler(ConfigInvalid)
    async def _config_invalid_handler(_request: Request, exc: ConfigInvalid) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "section": exc.section, "errors": [p.as_dict() for p in exc.problems]},
        )


# ── reading the models ────────────────────────────────────────────────────────────────────────


def _members(tp: Any) -> list[Any]:
    """The concrete types *tp* may be: ``Annotated`` and unions opened, ``None`` left out."""
    origin = get_origin(tp)
    if origin is Annotated:
        return _members(get_args(tp)[0])
    if origin is Union or origin is types.UnionType:
        out: list[Any] = []
        for arg in get_args(tp):
            if arg is not type(None):
                out.extend(_members(arg))
        return out
    return [tp]


def _is_model(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _model_field(model: type[BaseModel], key: str) -> Any | None:
    info = model.model_fields.get(key)
    if info is not None:
        return info
    for candidate in model.model_fields.values():
        if candidate.alias == key:
            return candidate
    return None


def _tagged(model: type[BaseModel], node: Any) -> bool:
    """Whether *node* names *model* through a ``type`` (or ``kind``) field holding a fixed value."""
    if not isinstance(node, dict):
        return False
    for tag in ("type", "kind"):
        info = model.model_fields.get(tag)
        if info is None or tag not in node:
            continue
        for member in _members(info.annotation):
            if get_origin(member) is Literal and node[tag] in get_args(member):
                return True
    return False


def _concrete(tp: Any, node: Any) -> type[BaseModel] | None:
    """The model *node* is validated against, among the ones *tp* allows."""
    models = [m for m in _members(tp) if _is_model(m)]
    if not models:
        return None
    for model in models:
        if _tagged(model, node):
            return model
    if isinstance(node, dict) and len(models) > 1:
        # no tag says which: the one that knows the most of the keys that were sent
        return max(models, key=lambda m: sum(1 for k in node if _model_field(m, str(k)) is not None))
    return models[0]


def _humanize(key: str) -> str:
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else key


def _label_of(node: Any, keys: tuple[str, ...]) -> str:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


@dataclass
class _Crumb:
    kind: str            # "field" | "key" | "index"
    text: str
    noun: str = ""


def _walk(loc: tuple[Any, ...], data: Any, model: type[BaseModel] | None) -> tuple[list[str | int], list[_Crumb]]:
    """Follow an error's location through the data that was sent and the model that refused it.
    Returns the path into the data (the segments Pydantic adds for a choice between shapes left
    out) and one crumb per step."""
    path: list[str | int] = []
    crumbs: list[_Crumb] = []
    node: Any = data
    tp: Any = model
    for i, seg in enumerate(loc):
        last = i == len(loc) - 1
        in_data = (isinstance(node, dict) and seg in node) or (
            isinstance(node, list) and isinstance(seg, int) and 0 <= seg < len(node))
        current = _concrete(tp, node) if tp is not None else None
        info = _model_field(current, seg) if current is not None and isinstance(seg, str) else None
        if not in_data and info is None:
            if last and isinstance(seg, str) and isinstance(node, dict) and tp is None:
                # no model to ask: a name that is not in the data is the field that is missing
                path.append(seg)
                crumbs.append(_Crumb("field", _humanize(seg)))
            # otherwise a segment Pydantic adds to say which shape of a choice it tried
            continue
        child = node[seg] if in_data else None
        if info is not None:
            path.append(seg)
            # the label the form shows for the field, when it is one of the generated forms'
            crumbs.append(_Crumb("field", (title_of(current, str(seg)) if current is not None else None)
                                 or info.title or _humanize(str(seg))))
            tp = info.annotation
        else:
            # a record of a map, or an element of a list
            next_tp: Any = None
            for member in (_members(tp) if tp is not None else []):
                origin = get_origin(member)
                args = get_args(member)
                if origin is dict and len(args) == 2 and isinstance(seg, str):
                    next_tp = args[1]
                    break
                if origin in (list, tuple, set, frozenset) and args and isinstance(seg, int):
                    next_tp = args[0]
                    break
            child_model = _concrete(next_tp, child) if next_tp is not None else None
            noun = _NOUNS.get(child_model.__name__, "") if child_model is not None else ""
            path.append(seg)
            if isinstance(seg, int):
                label = _label_of(child, ("name", "id", "label", "title")) or f"#{seg + 1}"
                crumbs.append(_Crumb("index", label, noun))
            else:
                label = _label_of(child, ("label", "title", "name")) if child_model is not None else ""
                crumbs.append(_Crumb("key", label or str(seg), noun))
            tp = next_tp
        node = child
    return path, crumbs


def _where(crumbs: list[_Crumb], root: type[BaseModel] | None) -> str:
    """The crumbs as one line: a collection's own name is left out before the record it holds."""
    parts: list[str] = []
    for i, crumb in enumerate(crumbs):
        nxt = crumbs[i + 1] if i + 1 < len(crumbs) else None
        if crumb.kind == "field" and nxt is not None and nxt.kind != "field":
            if nxt.noun or (i == 0 and root is not None):
                continue                      # "Columns › Column "B"" → "Column "B""; the file's own wrapper
        parts.append(f'{crumb.noun} "{crumb.text}"' if crumb.noun else crumb.text)
    return " › ".join(parts)


def _message(error: dict[str, Any]) -> str:
    code = str(error.get("type") or "")
    ctx = error.get("ctx") or {}
    template = _MESSAGES.get(code)
    if template is not None:
        try:
            return template.format(**{k: str(v).replace("'", "") for k, v in ctx.items()})
        except (KeyError, IndexError):
            pass
    msg = str(error.get("msg") or "").strip()
    for prefix in ("Value error, ", "Assertion failed, "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    return msg or "is not valid"


def problems_of(
    exc: ValidationError, data: Any = None, model: type[BaseModel] | None = None,
    *, prefix: tuple[str | int, ...] = (), prefix_where: str = "",
) -> list[Problem]:
    """The problems of a validation error, in the order Pydantic found them. *data* is what was
    validated and *model* the class that refused it; *prefix* is where that data sits in what the
    builder sent when only a part of it was validated (one pool, one connector)."""
    found: list[Problem] = []
    for error in exc.errors(include_url=False, include_input=False):
        loc = tuple(error.get("loc") or ())
        path, crumbs = _walk(loc, data, model)
        where = _where(crumbs, model)
        if prefix_where:
            where = f"{prefix_where} › {where}" if where else prefix_where
        code = str(error.get("type") or "")
        # one shape of a choice refused: "must be text" would name only the shape that was tried
        if code in _SHAPE_CODES and len(path) < len(loc):
            code = "model_type"
        message = _message({**error, "type": code})
        if code == "extra_forbidden" and crumbs:
            # the name that was sent is not a field: it is said in the message, not as a place
            message = f'"{path[-1]}" {message}'
            where = _where(crumbs[:-1], model)
            if prefix_where:
                where = f"{prefix_where} › {where}" if where else prefix_where
        found.append(Problem([*prefix, *path], where, message, code, [c.text for c in crumbs]))
    # A choice between shapes reports each shape it tried: a shape that is simply not the one sent
    # says nothing once another shape got further, and is said once otherwise.
    kept: list[Problem] = []
    shapes: set[tuple[str | int, ...]] = set()
    seen: set[tuple[tuple[str | int, ...], str]] = set()
    for problem in found:
        here = tuple(problem.path)
        if problem.code in _SHAPE_CODES:
            if here in shapes or any(len(o.path) > len(here) and tuple(o.path[:len(here)]) == here for o in found):
                continue
            shapes.add(here)
        key = (here, problem.message)
        if key in seen:
            continue
        seen.add(key)
        kept.append(problem)
    return kept


def _text(problems: list[Problem]) -> str:
    lines = [p.text for p in problems[:_TEXT_LINES]]
    if len(problems) > _TEXT_LINES:
        lines.append(f"… and {len(problems) - _TEXT_LINES} more")
    return "\n".join(lines)


def config_invalid(
    section: str, exc: Exception, data: Any = None, model: type[BaseModel] | None = None,
    *, prefix: tuple[str | int, ...] = (), prefix_where: str = "",
) -> ConfigInvalid:
    """The exception a save route raises when *section* is refused. A validation error becomes one
    line per problem; any other error (a rule a loader checks by hand) keeps its own sentence."""
    if isinstance(exc, ValidationError):
        problems = problems_of(exc, data, model, prefix=prefix, prefix_where=prefix_where)
        total = len(problems)
        problems = problems[:MAX_PROBLEMS]
        text = _text(problems)
        if total > MAX_PROBLEMS and total > _TEXT_LINES:
            text = "\n".join([*text.split("\n")[:_TEXT_LINES], f"… and {total - _TEXT_LINES} more"])
        return ConfigInvalid(section, problems, text or f"The {section} could not be saved.")
    message = str(exc).strip() or f"The {section} could not be saved."
    where = prefix_where
    return ConfigInvalid(section, [Problem(list(prefix), where, message, "value_error")],
                         f"{where}: {message}" if where else message)
