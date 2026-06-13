"""Per-user saved views — durable replacement for browser localStorage.

A *view* is a user's saved presentation of a thing: a grid's format (visible
columns + order + widths + sort + filters + grouping + page size) or a chart
spec. Stored per ``(username, kind, view_key)`` on the auth pool so it follows
the user across devices instead of living in one browser's localStorage.
"""
from __future__ import annotations

from liberty.userviews.store import UserView, UserViewStore

__all__ = ["UserView", "UserViewStore"]
