"""Screens — v2's port of v1's ``ly_tables`` + ``ly_dlg_frm`` + ``ly_dlg_tab`` + ``ly_dlg_col``
+ ``ly_dlg_filters`` (the dialog stack).

In v1 a *table* (``ly_tables``) referenced a *dialog form* (``ly_dlg_frm``) used to add/edit a
row. The form had tabs (``ly_dlg_tab``), each tab had fields (``ly_dlg_col``), and each field
could declare parameter bindings (``ly_dlg_filters``) for its lookup query. v2 collapses that
chain into one **Screen** entity per business object (e.g. ``F0005``, ``security_users``) —
the screen carries the read query + CRUD companions + an inline ``dialog`` definition with
tabs and fields. Same screen, same data model, no separate dialog table.

The ``ParamBind`` shape is the generalised v2 form of v1's ``ly_dlg_filters`` (and
``ly_tbl_filters``, and ``ly_dictionary_filters``): one entry binds a target query's
``:placeholder`` to either a literal ``value`` or a ``source`` (a column/field name resolved
at call time). It's used everywhere a query gets called from a screen — lookup combos in a
dialog, action arguments (slice 4), row context menus (slice 6) — so the framework has one
binding mechanism, not three.
"""

from __future__ import annotations

from liberty.screens.config import (
    Action,
    CallApiAction,
    ConfirmAction,
    FieldCondition,
    NavigateAction,
    NotifyAction,
    ParamBind,
    RefreshAction,
    RunQueryAction,
    Screen,
    ScreenDialog,
    ScreenField,
    ScreenTab,
    ScreensFile,
    SetFieldAction,
    load_screens,
    parse_screens,
)

__all__ = [
    "Action",
    "CallApiAction",
    "ConfirmAction",
    "FieldCondition",
    "NavigateAction",
    "NotifyAction",
    "ParamBind",
    "RefreshAction",
    "RunQueryAction",
    "Screen",
    "ScreenDialog",
    "ScreenField",
    "ScreenTab",
    "ScreensFile",
    "SetFieldAction",
    "load_screens",
    "parse_screens",
]
