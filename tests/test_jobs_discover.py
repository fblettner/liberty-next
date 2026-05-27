"""Tests for :mod:`liberty.jobs.discover` — AST-based python-step callable scan.

Uses ``tmp_path`` fixtures so the tests carry their own plugin set (one valid
module + one broken module + one with private helpers) and assert the
discovery contract independently of the real ``plugins/`` directory.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from liberty.jobs.discover import discover_callables


def _write(path: Path, src: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(src).lstrip("\n"), encoding="utf-8")


def test_discover_finds_async_and_sync_j_functions(tmp_path: Path) -> None:
    _write(tmp_path / "myplug" / "security.py", '''
        """A plugin module."""
        async def j_users():
            """Refresh users."""
            return 0

        def j_roles():
            """Refresh roles (sync)."""
            return 0

        def helper():
            """Not an entry point — wrong name prefix."""
    ''')

    out = discover_callables(tmp_path)
    callables = [c.callable for c in out]
    assert "myplug.security:j_users" in callables
    assert "myplug.security:j_roles" in callables
    assert "myplug.security:helper" not in callables  # no j_ prefix → skipped

    users = next(c for c in out if c.name == "j_users")
    assert users.is_async is True
    assert users.docstring == "Refresh users."

    roles = next(c for c in out if c.name == "j_roles")
    assert roles.is_async is False
    assert roles.docstring == "Refresh roles (sync)."


def test_discover_skips_nested_functions(tmp_path: Path) -> None:
    """A ``j_*`` defined inside a class or another function isn't a module-level
    attribute, so the executor's ``getattr(module, name)`` wouldn't find it —
    don't surface it in the dropdown either."""
    _write(tmp_path / "myplug" / "nested.py", '''
        class Container:
            def j_inside_class(self): pass

        def outer():
            async def j_inside_func(): pass
    ''')
    out = discover_callables(tmp_path)
    assert out == []


def test_discover_skips_private_files_and_pycache(tmp_path: Path) -> None:
    """Files starting with ``_`` (operator convention for "private helper") and
    anything under ``__pycache__/`` are skipped. ``__init__.py`` matches both
    rules; modules-as-packages still discover via the folder structure."""
    _write(tmp_path / "_private.py", '''
        async def j_should_not_show():
            return 0
    ''')
    _write(tmp_path / "pkg" / "__init__.py", '''
        async def j_should_not_show():
            return 0
    ''')
    _write(tmp_path / "pkg" / "__pycache__" / "cache.py", '''
        async def j_should_not_show():
            return 0
    ''')
    _write(tmp_path / "pkg" / "real.py", '''
        async def j_visible():
            return 0
    ''')

    out = discover_callables(tmp_path)
    names = {c.callable for c in out}
    assert names == {"pkg.real:j_visible"}


def test_discover_survives_a_broken_file(tmp_path: Path) -> None:
    """One file with a SyntaxError doesn't break the others — discovery is
    parse-only, so a broken plugin just logs a warning and is skipped while
    the working plugins still surface their callables."""
    _write(tmp_path / "good.py", '''
        async def j_works():
            return 0
    ''')
    # Deliberate syntax error.
    (tmp_path / "broken.py").write_text("def: this is not python\n", encoding="utf-8")

    out = discover_callables(tmp_path)
    names = {c.callable for c in out}
    assert "good:j_works" in names
    assert all("broken" not in n for n in names)


def test_discover_returns_empty_for_missing_root(tmp_path: Path) -> None:
    """A missing plugins directory returns an empty list (fresh install before
    any plugins land — no error, just no choices in the dropdown)."""
    assert discover_callables(tmp_path / "does-not-exist") == []


def test_discover_sorted_alphabetically(tmp_path: Path) -> None:
    """Stable ordering — the dropdown sees the same row order across reloads,
    so an operator's eye-position memory still works."""
    _write(tmp_path / "z.py", '''
        async def j_zeta(): return 0
        async def j_alpha(): return 0
    ''')
    _write(tmp_path / "a.py", '''
        async def j_beta(): return 0
    ''')
    out = discover_callables(tmp_path)
    assert [c.callable for c in out] == [
        "a:j_beta",
        "z:j_alpha",
        "z:j_zeta",
    ]
