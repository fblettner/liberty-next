from __future__ import annotations

from liberty.web.admin import router as admin_router
from liberty.web.connectors import router as connectors_router
from liberty.web.menus import router as menus_router

__all__ = ["admin_router", "connectors_router", "menus_router"]
