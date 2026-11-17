"""Firmware update service - always "no update available".

Logitech's directory lists ``SoftwareUpdateService/FirmwareInfo`` at
``https://sus.dhg.myharmony.com/SoftwareUpdatesPlatform/SoftwareUpdates/product/{productId}/unit/{unitId}/info``.
The response schema is unknown; we return an empty/no-update answer in a couple of
plausible shapes. Never pushing firmware is the safe default: a bad image can brick a hub
that can no longer be recovered from a vendor server.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

vhost_router = APIRouter(tags=["legacy: firmware"])
api_router = APIRouter(prefix="/api/firmware", tags=["firmware"])


def no_update(current: str | None = None) -> dict[str, Any]:
    return {
        "updateAvailable": False,
        "isUpdateAvailable": False,
        "currentVersion": current,
        "latestVersion": current,
        "updates": [],
        "imageUrl": None,
    }


@vhost_router.api_route(
    "/SoftwareUpdatesPlatform/SoftwareUpdates/product/{product_id}/unit/{unit_id}/info",
    methods=["GET", "POST"],
)
async def sus_info(product_id: str, unit_id: str, request: Request) -> dict[str, Any]:
    # TODO: validate against captured packet.
    return {"productId": product_id, "unitId": unit_id,
            **no_update(request.query_params.get("version"))}


@api_router.get("/check")
async def check(hubSwVersion: str | None = None) -> dict[str, Any]:  # noqa: N803
    return no_update(hubSwVersion)
