"""Logitech-compatible provisioning endpoints.

Mounted on the ``svcs`` and ``setup`` vhosts (see ``middleware/hostname_routing.py``).

Two families:

1. **hbus-style JSON** - ``POST /`` with ``{"id":1,"cmd":"setup.account?getProvisionInfo",
   "params":{}}``. This is the same command shape the hub serves locally on :8088 and the
   MyHarmony app sends to the hub. We answer it on the cloud hostnames so that (a) a hub
   asking the cloud for its provisioning gets a sane answer, and (b) our own tools can
   treat the server like a hub.

   Note: in the decompiled MyHarmony client, ``getProvisionInfo`` and ``provision`` are
   sent *to the hub* (locally). Whether a hub itself calls the cloud with this exact shape
   is NOT confirmed - see README "Known gaps".

2. **WCF json2 endpoints** named in Logitech's own service directory
   (``UserAccountDirector.svc/json2/ProvisionHub`` etc.). Operation names and response
   field names are from the decompiled data contracts; request bodies are best guesses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from harmony_server.auth.security import SCOPE_HARMONY, SCOPE_HUB, SCOPE_USER, decode_token
from harmony_server.db import Account, Hub
from harmony_server.deps import SessionDep, SettingsDep
from harmony_server.provisioning.hub_registry import HubRegistry
from harmony_server.provisioning.models import HbusRequest, ProvisionInfo

log = logging.getLogger(__name__)


def hbus_response(req: HbusRequest, code: int, msg: str, data: Any) -> dict[str, Any]:
    """Respond in both shapes seen in the wild.

    ``code/msg/data`` is what the hub returns on 8088 (parsed by aioharmony);
    ``errorCode/errorMessage/mime/result`` is what MyHarmony's
    ``JsonProvisioningRemoteResponse`` parses. Emitting both is harmless.
    """
    return {
        "id": req.id,
        "cmd": req.cmd,
        "code": code,
        "msg": msg,
        "data": data,
        "errorCode": str(code),
        "errorMessage": msg,
        "mime": req.cmd,
        "result": data,
    }


def _registry(request: Request) -> HubRegistry:
    reg: HubRegistry = request.app.state.registry
    return reg


async def _read_json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {"value": body}


def build_hbus_router() -> APIRouter:
    router = APIRouter(tags=["legacy: provisioning"])

    @router.post("/")
    async def hbus_dispatch(
        request: Request, session: SessionDep, settings: SettingsDep
    ) -> JSONResponse:
        req = HbusRequest.parse(await _read_json(request))
        obj, verb, inline = req.command()
        params = {**req.params, **inline}
        reg = _registry(request)
        client_ip = request.client.host if request.client else None
        auth = request.headers.get("authorization") or request.headers.get("x-harmony-cloud-auth")

        hub = await reg.identify(
            session,
            params=params,
            auth_header=auth,
            client_ip=client_ip,
            explicit_hub_id=req.hub_id or request.query_params.get("hubId"),
        )

        if obj == "account" and verb in ("getProvisionInfo", "getGlobalRemote", "isProvisioned"):
            # The cloud token is a secret: only hand it to a caller that presented it,
            # or to the caller that caused this hub record to be created just now.
            proven = False
            if hub is not None and auth:
                proven = reg.parse_cloud_auth(auth)[2] == hub.cloud_token
            if hub is None:
                if not settings.auto_register_hubs:
                    return JSONResponse(hbus_response(req, 404, "Unknown hub", None), 200)
                hub = await reg.register(
                    session,
                    hub_uuid=str(params.get("uuid") or params.get("hubUuid") or "") or None,
                    ip=client_ip,
                    sw_version=params.get("hubSwVersion"),
                )
                log.info("Auto-registered hub %s from %s", hub.global_remote_id, client_ip)
                proven = True
            await reg.touch(session, hub, client_ip, params)
            pdata = await reg.provision_data(session, hub, settings)
            if not proven:
                pdata.cloudAuth = pdata.token = None
            if verb == "getProvisionInfo":
                return JSONResponse(hbus_response(req, 200, "OK", pdata.model_dump()))
            if verb == "getGlobalRemote":
                return JSONResponse(
                    hbus_response(
                        req,
                        200,
                        "OK",
                        {
                            "globalRemoteId": pdata.globalRemoteId,
                            "activeRemoteId": pdata.activeRemoteId,
                            "accountId": pdata.accountId,
                            "friendlyName": hub.friendly_name,
                        },
                    )
                )
            return JSONResponse(
                hbus_response(
                    req,
                    200,
                    "OK",
                    {
                        "ProvisionStatus": str(hub.account_id is not None).lower(),
                        "ProvisionedEmail": pdata.email,
                    },
                )
            )

        if obj == "account" and verb == "provision":
            raw_info = params.get("provisionInfo", params)
            if isinstance(raw_info, str):
                try:
                    raw_info = json.loads(raw_info)
                except json.JSONDecodeError:
                    raw_info = {}
            info = ProvisionInfo.model_validate(raw_info or {})
            account_id = decode_token(info.authToken or "", settings, (SCOPE_HUB, SCOPE_USER))
            if account_id is None:
                return JSONResponse(hbus_response(req, 401, "Invalid authToken", None))
            if info.accountId and info.accountId != str(account_id):
                return JSONResponse(
                    hbus_response(req, 403, "accountId does not match authToken", None)
                )
            account = await session.get(Account, account_id)
            if account is None:
                return JSONResponse(hbus_response(req, 401, "Unknown account", None))
            for rid in (info.activeRemoteId, info.globalRemoteId):
                if hub is None and rid:
                    hub = await reg.get_by_remote_id(session, rid)
            if hub is None:
                hub = await reg.register(session, ip=client_ip, name=info.name)
            try:
                hub = await reg.claim(session, hub, account, info.name)
            except PermissionError as exc:
                return JSONResponse(hbus_response(req, 409, str(exc), None))
            if info.mode and info.mode.isdigit():
                hub.mode = int(info.mode)
                session.add(hub)
                await session.commit()
            pdata = await reg.provision_data(session, hub, settings)
            return JSONResponse(hbus_response(req, 200, "OK", pdata.model_dump()))

        return JSONResponse(hbus_response(req, 501, f"Unsupported command {req.cmd!r}", None))

    return router


def build_wcf_router() -> APIRouter:
    """WCF json2 operations from Logitech's service directory (best-effort bodies)."""
    router = APIRouter(tags=["legacy: WCF json2"])
    uad = "/UserAccountDirectorPlatform/UserAccountDirector.svc/json2"
    rm = "/RemotePlatform/RemoteManager.svc/json2"

    async def _account_from_request(
        request: Request, body: dict[str, Any], settings: SettingsDep
    ) -> int | None:
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        token = token or str(body.get("userAuthToken") or body.get("authToken") or "")
        return (
            decode_token(token, settings, (SCOPE_HUB, SCOPE_HARMONY, SCOPE_USER)) if token else None
        )

    async def _find_hub(request: Request, session: SessionDep, body: dict[str, Any]) -> Hub | None:
        flat: dict[str, Any] = dict(body)
        for v in body.values():
            if isinstance(v, dict):
                flat.update(v)
        return await _registry(request).identify(
            session,
            params=flat,
            auth_header=request.headers.get("authorization"),
            client_ip=request.client.host if request.client else None,
        )

    @router.post(f"{uad}/ProvisionHub")
    @router.post(f"{uad}/AddHubToAccount")
    @router.post(f"{rm}/AddHubToAccount")
    async def provision_hub(
        request: Request, session: SessionDep, settings: SettingsDep
    ) -> JSONResponse:
        # Contract: ProvisionHub(RemoteInfo, unitCertificate, PairingInfo[], ClientInfo)
        #        -> ProvisionHubResponse { HubCloudToken: string, HubId: long }
        body = await _read_json(request)
        account_id = await _account_from_request(request, body, settings)
        reg = _registry(request)
        hub = await _find_hub(request, session, body)
        if hub is None:
            cert = body.get("unitCertificate")
            hub = await reg.register(
                session,
                hub_uuid=str(cert) if cert else None,
                ip=request.client.host if request.client else None,
            )
        if account_id is not None:
            account = await session.get(Account, account_id)
            if account is not None:
                try:
                    hub = await reg.claim(session, hub, account)
                except PermissionError:
                    return JSONResponse({"error": "hub claimed by another account"}, 409)
        return JSONResponse({"HubCloudToken": hub.cloud_token, "HubId": hub.global_remote_id})

    @router.post(f"{uad}/GetHubCloudToken")
    async def get_hub_cloud_token(request: Request, session: SessionDep) -> JSONResponse:
        # Contract: GetHubCloudToken(unitCertificate) -> { HubCloudToken: string }
        body = await _read_json(request)
        hub = await _find_hub(request, session, body)
        if hub is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        return JSONResponse({"HubCloudToken": hub.cloud_token})

    @router.post(f"{uad}/GetGlobalRemote")
    async def get_global_remote(request: Request, session: SessionDep) -> JSONResponse:
        body = await _read_json(request)
        hub = await _find_hub(request, session, body)
        if hub is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        return JSONResponse(
            {
                "Id": hub.global_remote_id,
                "GlobalRemoteId": hub.global_remote_id,
                "AccountId": hub.account_id,
                "SerialNumber": hub.hub_uuid,
                "Name": hub.friendly_name,
            }
        )

    @router.post(f"{rm}/RegisterSuccessfulSync")
    @router.post(f"{uad}/Account/{{account_id}}/Remote/{{remote_id}}/RegisterSuccessfulSync")
    @router.post(f"{rm}/NotifySyncStatus")
    async def register_sync(request: Request, session: SessionDep) -> JSONResponse:
        body = await _read_json(request)
        remote_id = request.path_params.get("remote_id")
        if remote_id:
            body.setdefault("remoteId", remote_id)
        hub = await _find_hub(request, session, body)
        if hub is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        ver = body.get("configVersion") or body.get("ConfigVersion")
        if ver is not None and str(ver).isdigit():
            hub.synced_config_version = int(ver)
            session.add(hub)
            await session.commit()
        return JSONResponse({"Result": True})

    @router.post(f"{rm}/GetSyncStatus")
    async def get_sync_status(request: Request, session: SessionDep) -> JSONResponse:
        body = await _read_json(request)
        hub = await _find_hub(request, session, body)
        if hub is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        return JSONResponse({"SyncStatus": 0, "ConfigVersion": hub.synced_config_version})

    return router
