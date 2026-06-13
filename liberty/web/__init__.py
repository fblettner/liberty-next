from __future__ import annotations

from liberty.web.access import router as access_router
from liberty.web.actions import router as actions_router
from liberty.web.admin import router as admin_router
from liberty.web.changesets import router as changesets_router
from liberty.web.charts import router as charts_router
from liberty.web.dictgen import router as dictgen_router
from liberty.web.connectors import router as connectors_router
from liberty.web.dashboards import router as dashboards_router
from liberty.web.export import router as export_router
from liberty.web.jobs import router as jobs_router
from liberty.web.license import router as license_router
from liberty.web.menus import router as menus_router
from liberty.web.plugins import router as plugins_router
from liberty.web.reports import router as reports_router
from liberty.web.screens import router as screens_router
from liberty.web.theme import router as theme_router
from liberty.web.views import router as views_router

__all__ = [
    "access_router",
    "actions_router",
    "admin_router",
    "changesets_router",
    "charts_router",
    "dictgen_router",
    "connectors_router",
    "dashboards_router",
    "export_router",
    "jobs_router",
    "license_router",
    "menus_router",
    "plugins_router",
    "reports_router",
    "screens_router",
    "theme_router",
    "views_router",
]
