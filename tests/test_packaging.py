"""Packaging guards — what the wheel / image must carry to run the shipped defaults."""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _deps() -> list[str]:
    return tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]


def test_default_pool_driver_is_a_runtime_dependency() -> None:
    """The shipped ``[pools.default]`` URL is ``sqlite+aiosqlite://`` and the Light layout runs on
    it: aiosqlite must be a core dependency, not a dev extra (it was dev-only until 7.0.58 — the
    image could not open its own default pool, which disabled changesets / saved views / nomaflow
    on every Light install while the TOML auth backend kept the process booting)."""
    names = [d.split(">")[0].split("=")[0].split("[")[0].strip() for d in _deps()]
    for driver in ("aiosqlite", "asyncpg", "oracledb"):
        assert driver in names, f"{driver} missing from [project] dependencies"
