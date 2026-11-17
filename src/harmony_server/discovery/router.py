"""``svcs.myharmony.com`` Discovery + sync endpoints.

What ``/Discovery/Discovery.svc`` actually is
-------------------------------------------
The decompiled MyHarmony client and a captured response show it is a **WCF SOAP 1.1
service locator** (``IDiscovery``: GetAllServices, GetJson2Uris, GetJsonOperations,
GetSoapServices, GetBinaryOperations; each takes ``clientTypeId`` + ``clientId``). It
returns the URL of every other Logitech service. We serve the captured directory
(``data/discovery_services.json``) - that part is real, not a stub.

The hub is provisioned with ``discoveryServer = <...>/Discovery.svc``, so it almost
certainly resolves its sync/compile endpoints through this locator. *Which* endpoint the
hub then pulls its config from, and in what format, is unknown. We therefore also accept
a JSON body on Discovery.svc and answer with a best-effort config bundle (the stub the
project brief asked for), and implement ``CompileManager`` / bundle download endpoints.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from harmony_server.db import Hub
from harmony_server.deps import SessionDep, SettingsDep
from harmony_server.discovery.config_compiler import latest_config, sync_decision
from harmony_server.discovery.models import ConfigSyncRequest
from harmony_server.middleware.capture import capture_request
from harmony_server.provisioning.hub_registry import HubRegistry

log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "data" / "discovery_services.json"
TEMPURI = "http://tempuri.org/"
DC_NS = "http://schemas.datacontract.org/2004/07/Logitech.Harmony.Services.Discovery"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
MAX_SOAP_BODY = 64 * 1024

#: operation -> group in the captured directory
OPERATIONS: dict[str, str | None] = {
    "GetJson2Uris": "Json2Services",
    "GetJsonOperations": "JsonServices",
    "GetSoapServices": "SoapServices",
    "GetBinaryOperations": None,  # empty in every capture we have
    "GetAllServices": "*",
}


@lru_cache(maxsize=1)
def _captured() -> dict[str, list[dict[str, Any]]]:
    services: dict[str, list[dict[str, Any]]] = json.loads(DATA_FILE.read_text())["services"]
    return services


def service_directory(rewrites: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for group, items in _captured().items():
        rewritten = []
        for item in items:
            addr = item.get("Address")
            if addr:
                for old, new in rewrites.items():
                    addr = addr.replace(old, new)
            rewritten.append({**item, "Address": addr})
        out[group] = rewritten
    return out


def _sd_xml(items: list[dict[str, Any]]) -> str:
    parts = []
    for it in items:
        fields = []
        for k in ("Address", "EnvironmentId", "Identifier", "Name"):
            v = it.get(k)
            fields.append(f'<a:{k} i:nil="true"/>' if v is None else f"<a:{k}>{escape(v)}</a:{k}>")
        parts.append("<a:ServiceDescription>" + "".join(fields) + "</a:ServiceDescription>")
    return "".join(parts)


def soap_response(op: str, directory: dict[str, list[dict[str, Any]]]) -> str:
    group = OPERATIONS[op]
    if group == "*":
        inner = "".join(
            f"<a:{g}>{_sd_xml(directory.get(g, []))}</a:{g}>"
            for g in ("Json2Services", "JsonServices", "SoapServices")
        )
    else:
        inner = _sd_xml(directory.get(group, [])) if group else ""
    return (
        f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body>'
        f'<{op}Response xmlns="{TEMPURI}">'
        f'<{op}Result xmlns:a="{DC_NS}" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
        f"{inner}</{op}Result></{op}Response></s:Body></s:Envelope>"
    )


def parse_soap(body: bytes, soap_action: str | None) -> tuple[str | None, dict[str, str]]:
    """Return (operation, args). Operation from SOAPAction header, else the Body element."""
    op = None
    if soap_action:
        op = soap_action.strip('"').rsplit("/", 1)[-1] or None
    args: dict[str, str] = {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return op, args
    body_el = next((e for e in root.iter() if e.tag.endswith("}Body") or e.tag == "Body"), None)
    if body_el is not None and len(body_el):
        call = body_el[0]
        op = op or call.tag.rsplit("}", 1)[-1]
        for child in call:
            args[child.tag.rsplit("}", 1)[-1]] = child.text or ""
    return op, args


def _soap_fault(msg: str) -> Response:
    xml = (
        f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body><s:Fault><faultcode>s:Client</faultcode>'
        f"<faultstring>{escape(msg)}</faultstring></s:Fault></s:Body></s:Envelope>"
    )
    return Response(xml, status_code=500, media_type="text/xml; charset=utf-8")


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {"value": data}


async def _hub_for(
    request: Request, session: SessionDep, sync_req: ConfigSyncRequest, body: dict[str, Any]
) -> Hub | None:
    reg: HubRegistry = request.app.state.registry
    return await reg.identify(
        session,
        params={**body, **({"hubId": sync_req.hubId} if sync_req.hubId else {})},
        auth_header=request.headers.get("authorization"),
        client_ip=request.client.host if request.client else None,
    )


async def config_sync(request: Request, session: SessionDep, body: dict[str, Any]) -> JSONResponse:
    """Best-effort config sync.

    TODO: validate against captured Discovery.svc packet.
        Request field names (hubId/configVersion/contentVersion) come from the hub's state
        digest; the real request carrier, response envelope and bundle encoding are unknown.
    """
    sync_req = ConfigSyncRequest.from_any(body, dict(request.query_params))
    hub = await _hub_for(request, session, sync_req, body)
    if hub is None or hub.id is None:
        return JSONResponse({"code": 404, "msg": "Unknown hub", "data": None}, 404)
    reg: HubRegistry = request.app.state.registry
    await reg.touch(session, hub, request.client.host if request.client else None, body)

    latest = await latest_config(session, hub.id)
    decision = sync_decision(latest, sync_req.configVersion, sync_req.contentVersion)
    base = {"hubId": str(hub.global_remote_id), "syncRequired": decision == "update"}
    if decision == "none":
        return JSONResponse(
            {
                "code": 200,
                "msg": "No config stored",
                "data": {**base, "configVersion": 0, "contentVersion": 0},
            }
        )
    assert latest is not None
    if decision == "current":
        return JSONResponse(
            {
                "code": 200,
                "msg": "Config is current",
                "data": {
                    **base,
                    "configVersion": latest.config_version,
                    "contentVersion": latest.content_version,
                },
            }
        )
    return JSONResponse(
        {
            "code": 200,
            "msg": "OK",
            "data": {
                **base,
                "configVersion": latest.config_version,
                "contentVersion": latest.content_version,
                "config": json.loads(latest.payload_json),
            },
        }
    )


def build_router() -> APIRouter:
    router = APIRouter(tags=["legacy: discovery & sync"])
    cm = "/SyncPlatform/CompileManager.svc/json2"

    @router.get("/Discovery/Discovery.svc")
    async def discovery_get() -> Response:
        return Response(
            "<html><body>Discovery service (harmony-server). POST SOAP 1.1 "
            "(IDiscovery) or JSON.</body></html>",
            media_type="text/html",
        )

    @router.post("/Discovery/Discovery.svc")
    @router.post("/Discovery/Discovery.svc/basic")
    async def discovery_post(
        request: Request, session: SessionDep, settings: SettingsDep
    ) -> Response:
        body = await request.body()
        if settings.capture_unknown_requests:
            # Every real Discovery call is evidence (clientTypeId of the hub is unknown).
            await capture_request(
                request,
                settings.resolved_capture_dir,
                settings.capture_redact_headers,
                note="discovery",
            )
        ctype = request.headers.get("content-type", "")
        soap_action = request.headers.get("soapaction")
        if soap_action is not None or "xml" in ctype or body.lstrip()[:1] == b"<":
            if len(body) > MAX_SOAP_BODY:
                return _soap_fault("Request too large")
            op, args = parse_soap(body, soap_action)
            if op not in OPERATIONS:
                return _soap_fault(f"Unknown operation {op!r}")
            log.info(
                "Discovery.%s clientTypeId=%r clientId=%r",
                op,
                args.get("clientTypeId"),
                args.get("clientId"),
            )
            xml = soap_response(op, service_directory(settings.service_url_rewrites))
            return Response(xml, media_type="text/xml; charset=utf-8")
        return await config_sync(request, session, await _json_body(request))

    @router.api_route("/Discovery/Discovery.svc/{flavour}/{op}", methods=["GET", "POST"])
    async def discovery_json(flavour: str, op: str, settings: SettingsDep) -> JSONResponse:
        # Guess: WCF webHttp variants of the same contract. Unverified.
        if flavour not in ("json", "json2") or op not in OPERATIONS:
            raise HTTPException(404, "Unknown operation")
        directory = service_directory(settings.service_url_rewrites)
        group = OPERATIONS[op]
        if group == "*":
            return JSONResponse(directory)
        return JSONResponse(directory.get(group, []) if group else [])

    # -- CompileManager -------------------------------------------------------------
    @router.post(f"{cm}/IsRemoteSyncRequired")
    @router.post(f"{cm}/IsUserConfigChanged")
    async def is_sync_required(request: Request, session: SessionDep) -> JSONResponse:
        # TODO: validate against captured packet. Contract: IsRemoteSyncRequired(RemoteId)->bool
        body = await _json_body(request)
        sync_req = ConfigSyncRequest.from_any(body, dict(request.query_params))
        hub = await _hub_for(request, session, sync_req, body)
        if hub is None or hub.id is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        latest = await latest_config(session, hub.id)
        reported = (
            sync_req.configVersion
            if sync_req.configVersion is not None
            else hub.synced_config_version
        )
        return JSONResponse(sync_decision(latest, reported, None) == "update")

    @router.post(f"{cm}/StartCompile")
    @router.post(f"{cm}/StartCompileWithLocale")
    @router.post(f"{cm}/StartCompileWithLocaleAndSettings")
    async def start_compile(
        request: Request, session: SessionDep, settings: SettingsDep
    ) -> JSONResponse:
        """Contract returns ``CompileRequest{DownloadUrl, SimulationDownloadUrl, ApproximateSize}``.

        TODO: validate against captured packet. We do not compile anything new here - the
        latest stored bundle *is* the compile output - we just hand out its URL.
        """
        body = await _json_body(request)
        sync_req = ConfigSyncRequest.from_any(body, dict(request.query_params))
        hub = await _hub_for(request, session, sync_req, body)
        if hub is None or hub.id is None:
            return JSONResponse({"error": "unknown hub"}, 404)
        latest = await latest_config(session, hub.id)
        if latest is None:
            return JSONResponse({"error": "no config stored for hub"}, 409)
        url = (
            f"https://svcs.myharmony.com/SyncPlatform/compiled/"
            f"{hub.global_remote_id}/{latest.config_version}.json"
        )
        for old, new in settings.service_url_rewrites.items():
            url = url.replace(old, new)
        return JSONResponse(
            {
                "DownloadUrl": url,
                "SimulationDownloadUrl": url,
                "ApproximateSize": len(latest.payload_json),
                "ConfigVersion": latest.config_version,
            }
        )

    @router.post(f"{cm}/GetConnectedRemoteConfig")
    async def connected_remote_config(request: Request, session: SessionDep) -> JSONResponse:
        return await config_sync(request, session, await _json_body(request))

    @router.get("/SyncPlatform/compiled/{remote_id}/{version}.json")
    async def download_bundle(
        request: Request, remote_id: str, version: int, session: SessionDep
    ) -> Response:
        from sqlmodel import select

        from harmony_server.db import HarmonyConfig

        reg: HubRegistry = request.app.state.registry
        hub = await reg.get_by_remote_id(session, remote_id)
        if hub is None:
            raise HTTPException(404, "Unknown hub")
        row = (
            await session.exec(
                select(HarmonyConfig).where(
                    HarmonyConfig.hub_id == hub.id, HarmonyConfig.config_version == version
                )
            )
        ).first()
        if row is None:
            raise HTTPException(404, "Unknown config version")
        return Response(row.payload_json, media_type="application/json")

    return router
