from __future__ import annotations

from liberty.web.admin import router as admin_router
from liberty.web.charts import router as charts_router
from liberty.web.connectors import router as connectors_router
from liberty.web.dashboards import router as dashboards_router
from liberty.web.export import router as export_router
from liberty.web.jobs import router as jobs_router
from liberty.web.license import router as license_router
from liberty.web.menus import router as menus_router
from liberty.web.screens import router as screens_router
from liberty.web.theme import router as theme_router

__all__ = [
    "admin_router",
    "charts_router",
    "connectors_router",
    "dashboards_router",
    "export_router",
    "jobs_router",
    "license_router",
    "menus_router",
    "screens_router",
    "theme_router",
]
