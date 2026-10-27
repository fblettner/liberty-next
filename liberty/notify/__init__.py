"""Notifications — an inbox table, a dispatcher with portal / email / action channels, the
``/api/notifications`` routes (:mod:`liberty.web.notifications`). Rules that fire on screen and job
events come on top (``notifications.toml``); this package is the delivery layer."""

from __future__ import annotations

from liberty.notify.dispatcher import CHANNELS, ActionTarget, DispatchReport, Dispatcher, Message, Recipient, render
from liberty.notify.store import Notification, NotificationStore

__all__ = [
    "CHANNELS",
    "ActionTarget",
    "DispatchReport",
    "Dispatcher",
    "Message",
    "Notification",
    "NotificationStore",
    "Recipient",
    "render",
]
