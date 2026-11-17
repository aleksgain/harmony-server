"""Legacy MyHarmony app data endpoints (WCF json2), backed by our config store.

The desktop app's account/config screens call these after signing in. Paths are from
Logitech's service directory; payload shapes are PascalCase guesses mapped onto our
model. STUBS: enough for the app to get past "load account", not a full re-implementation
of Logitech's data model. Saves are captured to disk for analysis, not applied.

TODO: validate against captured packets (run in proxy mode to collect them).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from harmony_server.auth.legacy_router import COOKIE
from harmony_server.auth.security import SCOPE_HARMONY, SCOPE_USER, decode_token
from harmony_server.db import Account, Hub
from harmony_server.deps import SessionDep, SettingsDep
from harmony_server.discovery.config_compiler import latest_config
from harmony_server.middleware.capture import capture_request

AM = "/HarmonyPlatform/AccountManager.svc/json2"
UAD = "/UserAccountDirectorPlatform/UserAccountDirector.svc/json2"
ACT = "/ActivityPlatform/ActivityManager.svc/json2"
UBM = "/UserButtonMappingPlatform/UserButtonMappingManager.svc/json2"


def build_router() -> APIRouter:
    router = APIRouter(tags=["legacy: app data (stubs)"])

    async def _account(request: Request, session: SessionDep, settings: SettingsDep) -> Account | None:
        raw = request.headers.get("authorization") or request.cookies.get(COOKIE) or ""
        aid = decode_token(raw, settings, (SCOPE_HARMONY, SCOPE_USER))
        return await session.get(Account, aid) if aid is not None else None

    async def _hubs(session: SessionDep, account: Account) -> list[Hub]:
        from sqlmodel import select

        return list((await session.exec(select(Hub).where(Hub.account_id == account.id))).all())

    def _unauth() -> JSONResponse:
        return JSONResponse({"ErrorCode": 401, "Message": "Not signed in"}, 401)

    @router.api_route(f"{AM}/GetMyAccount", methods=["GET", "POST"])
    async def get_my_account(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct = await _account(request, session, settings)
        if acct is None:
            return _unauth()
        hubs = await _hubs(session, acct)
        return JSONResponse({
            "Id": acct.id, "AccountId": acct.id, "Email": acct.email,
            "Properties": {"Email": acct.email, "Country": "", "Language": "en-US"},
            "Remotes": [{"Id": h.global_remote_id, "Name": h.friendly_name,
                         "SerialNumber": h.hub_uuid, "SkinId": 97} for h in hubs],
        })

    @router.api_route(f"{AM}/GetMyHousehold", methods=["GET", "POST"])
    async def get_my_household(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct = await _account(request, session, settings)
        if acct is None:
            return _unauth()
        return JSONResponse({"Id": acct.id, "Accounts": [{"Id": acct.id, "Email": acct.email}]})

    async def _config_for(request: Request, session: SessionDep, settings: SettingsDep) -> tuple[Account | None, dict[str, Any]]:
        acct = await _account(request, session, settings)
        if acct is None:
            return None, {}
        hubs = await _hubs(session, acct)
        if not hubs or hubs[0].id is None:
            return acct, {}
        latest = await latest_config(session, hubs[0].id)
        return acct, json.loads(latest.payload_json) if latest else {}

    @router.api_route(f"{UAD}/GetDeviceList", methods=["GET", "POST"])
    @router.api_route(f"{UAD}/SimpleRestGetDeviceList", methods=["GET", "POST"])
    async def get_device_list(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct, cfg = await _config_for(request, session, settings)
        if acct is None:
            return _unauth()
        return JSONResponse([{
            "Id": d["id"], "Name": d["label"], "Manufacturer": d.get("manufacturer"),
            "Model": d.get("model"), "Type": d.get("type"), "GlobalDeviceId": d.get("globalDeviceId"),
        } for d in cfg.get("device", [])])

    @router.api_route(f"{UAD}/GetActivities", methods=["GET", "POST"])
    @router.api_route(f"{UAD}/SimpleRestGetActivities", methods=["GET", "POST"])
    @router.api_route(f"{ACT}/GetActivities", methods=["GET", "POST"])
    async def get_activities(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct, cfg = await _config_for(request, session, settings)
        if acct is None:
            return _unauth()
        return JSONResponse([{
            "Id": a["id"], "Name": a["label"], "Type": a.get("type"),
            "Devices": list(a.get("fixit", {}).keys()),
        } for a in cfg.get("activity", []) if a["id"] != "-1"])

    @router.api_route(f"{UAD}/GetButtonMaps", methods=["GET", "POST"])
    @router.api_route(f"{UBM}/GetButtonMaps", methods=["GET", "POST"])
    async def get_button_maps(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct, cfg = await _config_for(request, session, settings)
        if acct is None:
            return _unauth()
        return JSONResponse([{"ActivityId": a["id"], "ControlGroups": a.get("controlGroup", [])}
                             for a in cfg.get("activity", [])])

    @router.api_route(f"{UAD}/SaveActivities", methods=["GET", "POST"])
    @router.api_route(f"{ACT}/SaveActivities", methods=["GET", "POST"])
    @router.api_route(f"{UBM}/SaveButtonMaps", methods=["GET", "POST"])
    async def save_stub(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        acct = await _account(request, session, settings)
        if acct is None:
            return _unauth()
        await capture_request(request, settings.resolved_capture_dir,
                              settings.capture_redact_headers, note="legacy-save (not applied)")
        return JSONResponse({"Result": True, "Applied": False,
                             "Message": "Captured; use /api/hubs/{id}/config to edit"})

    for svc in (AM, UAD, ACT, UBM, "/RemotePlatform/RemoteManager.svc/json2",
                "/SyncPlatform/CompileManager.svc/json2"):
        router.add_api_route(f"{svc}/Ping", lambda: JSONResponse("pong"), methods=["GET", "POST"])

    return router
