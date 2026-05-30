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


@router.get(
    "/license",
    summary="License status",
    responses={
        401: {"description": "Missing / invalid access token."},
    },
)
async def license_status(principal: CurrentPrincipal, request: Request) -> dict[str, Any]:
    """Returns ``{mode, customer, plan, apps, expires_at, error}``:

    - **mode** — ``full`` when ``LIBERTY_LICENSE_KEY`` decodes + verifies + isn't expired;
      ``restricted`` otherwise. Licensed connectors (``licensed = true`` in the apps
      repo's connectors.toml) load only in ``full`` mode.
    - **error** — populated in ``restricted`` mode with the verify-failure reason
      (expired / signature mismatch / decode error / missing key).
    - **apps** — the list of app ids the key unlocks (e.g. ``["nomasx1", "nomajde"]``).
    - **expires_at** — ISO-8601 UTC timestamp.

    The license is verified once at startup (``app.state.license``) and re-verified on
    ``POST /admin/reload``. ``GET /info`` exposes a subset (just ``mode``) without auth
    for liveness probes."""
    result: LicenseResult = request.app.state.license
    return result.public_dict()
