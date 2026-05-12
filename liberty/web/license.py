"""``GET /api/license`` — the license status (mode + claims), for any authenticated user.

The frontend reads it after login to know whether it's running ``"full"`` (a valid key — the
licensed connectors loaded) or ``"restricted"`` (no/invalid/expired key — they didn't load), and
shows a banner / the customer & expiry. The license itself is verified once at startup
(``app.state.license``) and re-verified on ``POST /admin/reload``. ``/info`` exposes just the
``mode`` publicly; the customer/email/expiry are behind auth here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from liberty.auth.dependencies import CurrentPrincipal
from liberty.licensing import LicenseResult

router = APIRouter(prefix="/api", tags=["license"])


@router.get("/license")
async def license_status(principal: CurrentPrincipal, request: Request) -> dict[str, Any]:
    result: LicenseResult = request.app.state.license
    return result.public_dict()
