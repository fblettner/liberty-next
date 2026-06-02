"""Runtime kwargs coercion — shared between nomaflow python steps and reports.

The framework receives kwargs as strings whenever the value crosses a "stringly
typed" boundary — TOML form inputs, JSON request bodies from the UI, query
strings. Python annotations declare the target type; this module bridges the
two with explicit, predictable rules:

* ``int`` / ``float`` / ``bool`` / ``str`` — coerce via the obvious constructor
  (with ``true`` / ``yes`` / ``1`` etc. accepted for bool)
* ``Optional[X]`` / ``X | None`` — unwrap and recurse to ``X``
* parameterized generics (``list[str]``, ``dict[str, int]``, …) — accept the
  value if it matches the runtime origin; we don't recurse into contained types
* anything else (dataclasses, NewType, custom classes) — pass through

The same helpers serve :func:`liberty.jobs.steps.python_step._build_kwargs`
and :func:`liberty.web.reports.run_report` so a string ``"1"`` becomes ``1``
the same way whether it came from a job's ``op_kwargs`` or a report's
``params`` body.
"""
from __future__ import annotations

import inspect
import types
import typing
from typing import Any, Callable, Union

_BOOL_TRUE = frozenset({"true", "t", "yes", "y", "1", "on"})
_BOOL_FALSE = frozenset({"false", "f", "no", "n", "0", "off"})


class CoercionError(ValueError):
    """A kwargs value couldn't be coerced to its annotated type.

    Carries the offending ``key``, the raw ``value``, the target ``annotation``,
    and the underlying ``cause`` (``TypeError`` or ``ValueError`` from the
    primitive coercion). Callers format the user-facing message in their own
    voice — ``op_kwargs[...]`` for jobs, ``params[...]`` for reports — by
    reading the attributes.
    """

    def __init__(self, key: str, value: Any, annotation: Any, cause: Exception) -> None:
        self.key = key
        self.value = value
        self.annotation = annotation
        self.cause = cause
        super().__init__(
            f"{key!r}={value!r} cannot be coerced to "
            f"{annotation_name(annotation)}: {cause}"
        )


def coerce_to_annotation(value: Any, annotation: Any) -> Any:
    """Coerce *value* to *annotation* (a type, ``X | None``, ``Optional[X]``, or
    ``Union[...]``). Returns *value* unchanged when:

    * it's already an instance of the annotation
    * the annotation is one we don't know how to coerce to (custom dataclass,
      generic container, …) — pass-through is safer than guessing.

    Raises ``TypeError`` / ``ValueError`` when the value can't reach the target
    type (e.g. ``int("abc")``). The caller normally wraps these in
    :class:`CoercionError` with the parameter name attached.
    """
    if value is None:
        return None

    origin = typing.get_origin(annotation)
    # ``Union[X, None]`` (typing) and ``X | None`` (PEP 604) have different origins:
    # ``typing.Union`` vs ``types.UnionType`` — accept both.
    if origin is Union or origin is types.UnionType:
        # X | None or Union[X, Y]: try each arg in order (None already handled above).
        # If the value is already an instance of one of them, accept; otherwise
        # try coercion against the first non-None type — Optional[int] coerces to int.
        # ``isinstance(value, list[str])`` raises TypeError on parameterized generics —
        # use the runtime origin (``list``) for the check; pass through plain types as-is.
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        for arg in non_none:
            check_type = typing.get_origin(arg) or arg
            if isinstance(check_type, type) and isinstance(value, check_type):
                return value
        if non_none:
            return coerce_to_annotation(value, non_none[0])
        return value

    # Parameterized generic at the top level (``list[str]``, ``dict[str, int]``, …).
    # Same isinstance() trap as in the Union branch — fall back to the runtime origin.
    if origin is not None:
        if isinstance(origin, type) and isinstance(value, origin):
            return value
        # We don't try to coerce the contained types (list-of-str etc.) — pass through.
        return value

    # Plain types only from here on. Already the right type → pass through.
    if not isinstance(annotation, type):
        return value
    if isinstance(value, annotation):
        # bool is an int subclass — guard so a literal True doesn't sneak past
        # an ``int`` annotation as 1 (it would, but at least flag bools intended
        # for non-bool slots).
        if annotation is int and isinstance(value, bool):
            return int(value)
        return value

    if annotation is bool:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _BOOL_TRUE:
                return True
            if v in _BOOL_FALSE:
                return False
            raise ValueError(f"{value!r} is not a boolean")
        if isinstance(value, (int, float)):
            return bool(value)
        raise TypeError(f"can't coerce {type(value).__name__} to bool")
    if annotation is int:
        if isinstance(value, str):
            return int(value.strip())
        if isinstance(value, float):
            return int(value)
        raise TypeError(f"can't coerce {type(value).__name__} to int")
    if annotation is float:
        if isinstance(value, (str, int)):
            return float(value if not isinstance(value, str) else value.strip())
        raise TypeError(f"can't coerce {type(value).__name__} to float")
    if annotation is str:
        return str(value)

    # Anything else (list / dict / dataclass / NewType / etc.) — pass through.
    return value


def coerce_kwargs(kwargs: dict[str, Any], target: Callable[..., Any]) -> dict[str, Any]:
    """Coerce every *kwargs* value against the matching parameter's annotation
    on *target*. Returns a new dict — *kwargs* is untouched.

    Skip kwargs that don't correspond to a declared parameter (they'll land in
    ``**kwargs`` without a per-key annotation). ``eval_str=True`` so PEP 563
    string annotations (``from __future__ import annotations``) resolve to
    real types — without it ``param.annotation`` is the *string* ``"int"`` and
    the coercion below would silently skip.

    Raises :class:`CoercionError` on the first value that can't reach its
    annotated type.
    """
    out = dict(kwargs)
    try:
        sig = inspect.signature(target, eval_str=True)
    except (TypeError, ValueError, NameError):  # pragma: no cover — builtins / undefined names
        return out
    params = sig.parameters
    for key in list(out):
        param = params.get(key)
        if param is None or param.annotation is inspect.Parameter.empty:
            continue
        try:
            out[key] = coerce_to_annotation(out[key], param.annotation)
        except (TypeError, ValueError) as exc:
            raise CoercionError(key, out[key], param.annotation, exc) from exc
    return out


def annotation_name(annotation: Any) -> str:
    """Best-effort short name for error messages — ``int``, ``int | None``, ``str``."""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")
