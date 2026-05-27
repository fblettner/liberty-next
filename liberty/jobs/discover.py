"""AST-based discovery of python-step callables from the ``plugins/`` tree.

The nomaflow runner resolves a step's ``callable = "module:function"`` at fire
time via :func:`importlib.import_module` + :func:`getattr` (see
:mod:`liberty.jobs.steps.python_step`). Operators authoring jobs through the
Job Designer want to PICK a callable from a dropdown rather than typing the
full ``module:function`` string by hand — same as every other reference field
in Liberty (queries / screens / dashboards / charts). This module supplies the
catalog the dropdown reads from.

**Discovery strategy** — walk ``plugins/`` for ``*.py`` files, parse each with
the stdlib ``ast`` module, find every top-level ``async def`` / ``def`` whose
NAME matches the convention ``j_<something>`` (the established v2 convention
for python-step entry points). Returns metadata (callable id, module path,
docstring) — no imports happen, so a broken plugin doesn't break discovery.

Why AST and not ``importlib.import_module``:

* **Fast** — string parse beats Python's import machinery (which would execute
  module top-level code, pulling in SQLAlchemy / oracledb / ldap3 / …).
* **Safe** — never executes plugin code. A plugin with a fatal import error
  still shows its callables in the dropdown; only the run-time invocation
  fails (where the operator gets a clear traceback instead of a silent
  "callable missing" 404 at design time).
* **No caching needed** — for the current plugin set (~30 files, ~70 callables)
  a fresh walk costs a few ms. If the catalog grows we can cache per app
  startup; the cost so far doesn't justify the invalidation complexity.

The ``j_`` convention is enforced by docstring + tests in the existing nomasx1
plugin set; future plugins should follow it for the Job Designer dropdown to
surface them. Callables that don't follow the convention can still be wired
via the freeform path (the dropdown's ``allowCustom`` lets operators type a
callable that wasn't discovered)."""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Callable:
    """One discovered python-step entry point — what the Job Designer surfaces
    in its callable dropdown.

    Fields mirror ``liberty.jobs.schema.Step.callable`` (a ``"module:function"``
    string) plus the bits the UI needs to render a useful row label + hover
    tooltip without a second round-trip per callable."""

    callable: str        # ``"module:function"`` — what goes into Step.callable
    module: str          # dotted module path, e.g. ``"nomasx1.security"``
    name: str            # function name, e.g. ``"j_security_users"``
    is_async: bool       # async vs sync (executor handles both; just informational)
    docstring: str | None  # first line of the function's docstring (None when missing)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_callables(plugins_root: Path) -> list[Callable]:
    """Walk *plugins_root* and return every ``j_<name>`` entry point found.

    Each ``.py`` file is parsed independently; a file with a syntax error logs
    a warning and is skipped (the others still discover normally). The returned
    list is sorted alphabetically by ``callable`` for deterministic ordering in
    the UI.

    Module paths are derived from the file path relative to *plugins_root* —
    ``plugins/nomasx1/security.py`` → ``nomasx1.security``. Honours
    ``__init__.py`` (folder becomes a package, file is the package itself, not
    a separate ``<pkg>.__init__`` entry).

    Skips:
      * Files under ``__pycache__/``
      * Files starting with ``_`` (operator convention for "private helpers")
      * The dunder ``__init__.py`` of any package
      * Files outside the ``plugins/`` root after symlink resolution (defensive)
    """
    if not plugins_root.is_dir():
        return []
    results: list[Callable] = []
    for py in plugins_root.glob("**/*.py"):
        # Filter out the housekeeping + private files BEFORE doing any work.
        if "__pycache__" in py.parts:
            continue
        if py.name.startswith("_"):
            # Also catches ``__init__.py`` since "__" starts with "_".
            continue
        # Defensive: resolve and confirm still inside plugins_root (symlink guard).
        try:
            resolved = py.resolve()
            resolved.relative_to(plugins_root.resolve())
        except ValueError:
            continue

        rel = py.relative_to(plugins_root).with_suffix("")
        module = ".".join(rel.parts)
        try:
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py))
        except (OSError, SyntaxError) as exc:
            # Read or parse failure: skip this file but keep walking — one
            # broken plugin shouldn't hide the others from the operator.
            _log.warning("liberty.jobs.discover skip %s: %s", py, exc)
            continue

        # Top-level only — nested functions / methods inside classes aren't
        # python-step callables (the executor calls ``getattr(module, name)``
        # which only sees module-level attributes).
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not node.name.startswith("j_"):
                continue
            results.append(Callable(
                callable=f"{module}:{node.name}",
                module=module,
                name=node.name,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                docstring=_first_line(ast.get_docstring(node)),
            ))

    results.sort(key=lambda c: c.callable)
    return results


def _first_line(text: str | None) -> str | None:
    """First non-empty line of a docstring — what we show as the dropdown's row
    subtitle / hover tooltip. The whole docstring would dominate a SearchSelect
    row; one line is enough to disambiguate two ``j_*`` callables sharing a
    similar name."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
