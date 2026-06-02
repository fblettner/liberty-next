"""Discover + hold the set of registered :class:`ReportDef` instances.

Population at startup
---------------------
* Walk ``LIBERTY_APPS_DIR/../plugins/<scope>/reports/`` (one directory per
  plugin), import ``<scope>.reports`` (Python package), read its ``REPORTS``
  constant (must be a list of :class:`ReportDef`).
* For each ``ReportDef``: drop it when ``licensed=True`` and the install's
  :class:`~liberty.licensing.LicenseResult` doesn't cover ``def.scope``.
* Resolve the ``callable`` string once into a real function reference (so a
  typo trips at startup, not at first request).
* Reject duplicate ``scope:id`` pairs across all plugins.

The registry exposes a tiny public API (:meth:`names`, :meth:`get`,
:meth:`list_for`) consumed by the web layer; :meth:`get_callable` is reserved
for the dispatcher.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from liberty.licensing import LicenseResult
from liberty.reports.schema import ReportDef, UnknownReportError

_log = logging.getLogger(__name__)


class ReportRegistry:
    """The in-memory registry of available reports.

    Built by :func:`discover_reports` at startup and stored on
    ``app.state.reports``. Replaced atomically by :func:`refresh_reports`
    when ``/admin/reload`` fires.
    """

    def __init__(
        self,
        defs: Iterable[ReportDef] = (),
        *,
        callables: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._defs: dict[str, ReportDef] = {}
        self._callables: dict[str, Callable[..., Any]] = dict(callables or {})
        for d in defs:
            key = self._key(d.scope, d.id)
            if key in self._defs:
                raise ValueError(
                    f"duplicate report id {key!r} — already declared by "
                    f"{self._defs[key].callable!r}, also by {d.callable!r}"
                )
            self._defs[key] = d

    @staticmethod
    def _key(scope: str, report_id: str) -> str:
        return f"{scope}:{report_id}"

    def names(self) -> list[str]:
        """All ``scope:id`` keys (sorted) — handy for ``/admin/reload`` logs."""
        return sorted(self._defs)

    def all(self) -> list[ReportDef]:
        """Every report definition, sorted by ``scope:id``."""
        return [self._defs[k] for k in sorted(self._defs)]

    def list_for(self, scope: str | None = None) -> list[ReportDef]:
        """Reports filtered by scope (None → all). Used by the list endpoint."""
        out = self.all()
        if scope is None:
            return out
        return [d for d in out if d.scope == scope]

    def get(self, scope: str, report_id: str) -> ReportDef:
        """The :class:`ReportDef` for ``scope:id``. Raises :class:`UnknownReportError`
        (mapped to HTTP 404 by the web layer)."""
        key = self._key(scope, report_id)
        d = self._defs.get(key)
        if d is None:
            raise UnknownReportError(
                f"unknown report {key!r}. Available: {self.names() or '(none)'}"
            )
        return d

    def get_callable(self, scope: str, report_id: str) -> Callable[..., Any]:
        """The resolved python callable for ``scope:id`` — populated at
        :func:`discover_reports` time so a typo trips fast.

        Raises :class:`UnknownReportError` when the report isn't registered,
        :class:`LookupError` when the report exists but its callable wasn't
        resolved (defensive — shouldn't happen post-discovery)."""
        self.get(scope, report_id)  # 404 path
        key = self._key(scope, report_id)
        fn = self._callables.get(key)
        if fn is None:
            raise LookupError(
                f"report {key!r} has no resolved callable — registry built without it?"
            )
        return fn

    def add(
        self,
        defs: Iterable[ReportDef],
        callables: dict[str, Callable[..., Any]],
    ) -> None:
        """Merge an extra batch of (defs, callables) into this registry —
        used by the wiring layer to fold operator-authored custom reports
        (Phase 4) in alongside plugin-discovered ones.

        Duplicate ``scope:id`` collisions raise :class:`ValueError` so a
        custom report can't shadow a plugin one (and vice versa). Same
        contract as :meth:`__init__`."""
        for d in defs:
            key = self._key(d.scope, d.id)
            if key in self._defs:
                raise ValueError(
                    f"duplicate report id {key!r} — already declared by "
                    f"{self._defs[key].callable!r}, also by {d.callable!r}"
                )
            self._defs[key] = d
        for k, fn in callables.items():
            self._callables[k] = fn


def _resolve_callable(spec: str) -> Callable[..., Any]:
    """Resolve a ``module.path:function`` string to a real callable.

    Raises :class:`ImportError` (missing module) or :class:`AttributeError`
    (missing function) with a clear message — :func:`discover_reports` lets
    them propagate so a typo trips at app startup, not at first request.
    """
    module_path, _, fn_name = spec.partition(":")
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)
    if not callable(fn):
        raise TypeError(f"{spec!r} resolves to {type(fn).__name__}, not callable")
    return fn


def _iter_plugin_report_modules(plugins_dir: Path) -> Iterable[tuple[str, str]]:
    """Yield ``(plugin_name, module_dotted_path)`` for every plugin that ships
    a ``reports`` package.

    A plugin "ships reports" iff ``plugins/<name>/reports/__init__.py`` exists.
    We import the package (e.g. ``nomasx1.reports``), expecting its
    ``__init__.py`` to expose a ``REPORTS: list[ReportDef]``.
    """
    if not plugins_dir.exists():
        return
    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_", "__")):
            continue
        if (child / "reports" / "__init__.py").is_file():
            yield child.name, f"{child.name}.reports"


def discover_reports(
    plugins_dir: Path | str | None = None,
    *,
    license: LicenseResult | None = None,
) -> ReportRegistry:
    """Walk *plugins_dir*, import each ``<scope>.reports`` package, collect
    its ``REPORTS`` list, build a fresh :class:`ReportRegistry`.

    *plugins_dir* defaults to ``$LIBERTY_APPS_DIR/../plugins`` (same convention
    as :func:`liberty.main.ensure_plugins_on_sys_path`). When ``LIBERTY_APPS_DIR``
    is unset and *plugins_dir* is None, the discovery is a no-op (empty
    registry). When ``license`` is provided, defs with ``licensed=True`` whose
    scope isn't covered are dropped — same gating as for licensed connectors.
    """
    if plugins_dir is None:
        apps = os.environ.get("LIBERTY_APPS_DIR", "").strip()
        if not apps:
            return ReportRegistry()
        plugins_dir = Path(apps).parent / "plugins"
    plugins_dir = Path(plugins_dir)

    # Make sure plugins/ is importable — same hook the framework uses everywhere
    # else (ensure_plugins_on_sys_path). Idempotent.
    plugins_dir_str = str(plugins_dir)
    if plugins_dir_str not in sys.path:
        sys.path.insert(0, plugins_dir_str)

    defs: list[ReportDef] = []
    callables: dict[str, Callable[..., Any]] = {}

    for scope, module_path in _iter_plugin_report_modules(plugins_dir):
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 — surface the import failure but keep going
            _log.warning(
                "reports: failed to import %s.reports — %s: %s",
                scope, type(exc).__name__, exc,
            )
            continue

        plugin_defs: list[ReportDef] = list(getattr(module, "REPORTS", []) or [])
        if not plugin_defs:
            _log.info("reports: %s.reports has no REPORTS — skipped", scope)
            continue

        loaded = 0
        gated = 0
        for d in plugin_defs:
            # Same gating rule as licensed connectors (load_connectors): a
            # ``licensed=True`` report loads only when a license that covers
            # the scope is configured. ``license=None`` (no license at all)
            # therefore filters every licensed report out.
            if d.licensed and not (license is not None and license.covers(d.scope)):
                gated += 1
                continue
            try:
                fn = _resolve_callable(d.callable)
            except (ImportError, AttributeError, TypeError) as exc:
                raise RuntimeError(
                    f"reports: cannot resolve {d.callable!r} for {d.scope}:{d.id}: {exc}"
                ) from exc
            defs.append(d)
            callables[f"{d.scope}:{d.id}"] = fn
            loaded += 1

        if gated:
            _log.info(
                "reports: skipped %d licensed report(s) from %s (no covering license)",
                gated, scope,
            )
        _log.info("reports: loaded %d report(s) from %s", loaded, scope)

    return ReportRegistry(defs, callables=callables)
