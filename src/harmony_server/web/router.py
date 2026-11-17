"""setup.myharmony.com compatibility layer.

The legacy Silverlight client's launcher checks serviceConfig.xml for the WebURL,
then loads harmonyconfig — an HTML page that bootstraps the XAP.  For DNS-rewrite
mode we serve just enough of this to let the launcher open the page; the actual
Silverlight XAP is fetched from files.myharmony.com (still live as of 2026-11-17).

Also handles:
  POST /martiniweb/account/ProceedWithLIPLogin  — Logi ID proxy (step 1 of auth)
  POST /martiniweb/account/Refresh_RefreshToken — Token refresh proxy
  GET  /martiniweb/account/GetLIPUserInfo       — OIDC userinfo proxy
  GET  /martiniweb/account/harmonyconfig        — Launcher landing page (302 → /MartiniWeb/...)
  GET  /MartiniWeb/account/harmonyconfig        — Bootstrap HTML with initparams
  GET  /prod/serviceConfig.xml                  — Launcher update/config check
  GET  /remoterecoverytool/DefaultRRT.html      — RRT stub

For the Logi ID endpoints we act as a transparent proxy to accounts.logi.com so
DNS-rewrite users do not need to whitelist that domain separately.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

router = APIRouter(tags=["web-compat"])

# ---------------------------------------------------------------------------
# Launcher config
# ---------------------------------------------------------------------------

# Served at http://app.myharmony.com/prod/serviceConfig.xml
# Tells launcher: app is up to date, no forced update.
_SERVICE_CONFIG_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<config>
  <LogglyURL>http://localhost/noop</LogglyURL>
  <WebURL>https://setup.myharmony.com/account/harmonyconfig</WebURL>
  <RRTURL>https://setup.myharmony.com/remoterecoverytool/DefaultRRT.html</RRTURL>
  <WinUpdateVersion>1.0.1.308</WinUpdateVersion>
</config>
"""


@router.get("/prod/serviceConfig.xml")
async def service_config() -> Response:
    return Response(content=_SERVICE_CONFIG_XML, media_type="application/xml")


# ---------------------------------------------------------------------------
# Landing page redirect (mirrors setup.myharmony.com 302)
# ---------------------------------------------------------------------------

@router.get("/account/harmonyconfig")
@router.get("/martiniweb/account/harmonyconfig")
async def harmonyconfig_redirect() -> RedirectResponse:
    return RedirectResponse(url="/MartiniWeb/account/harmonyconfig", status_code=302)


# ---------------------------------------------------------------------------
# Bootstrap HTML  (MartiniWeb/account/harmonyconfig)
# The real page serves Silverlight with an embedded <object> tag and initparams.
# Our version points the XAP at the live files.myharmony.com CDN so the Silverlight
# app itself is still served by Logitech while the API calls go to us.
# ---------------------------------------------------------------------------

_HARMONY_CONFIG_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MyHarmony — Harmony Revival</title>
  <style>body{{background:#1a1a2e;color:#eee;font-family:sans-serif;text-align:center;padding:40px}}</style>
</head>
<body>
  <h1>Harmony Revival</h1>
  <p>Self-hosted MyHarmony backend active.</p>
  <p>The Silverlight plugin is loaded from files.myharmony.com (still live).</p>
  <object id="harmonyPlugin"
    type="application/x-silverlight-2"
    width="100%" height="100%"
    data="data:application/x-silverlight-2,">
    <param name="source" value="https://files.myharmony.com/Assets/ClientBin/Web.MartiniWeb.Core.xap"/>
    <param name="onError" value="onSilverlightError"/>
    <param name="background" value="#1a1a2e"/>
    <param name="minRuntimeVersion" value="5.0.61118.0"/>
    <param name="autoUpgrade" value="true"/>
    <param name="initParams" value="discoveryServiceUrl=https://svcs.myharmony.com/Discovery,assets=https://files.myharmony.com/Assets/,ContentServiceURL=https://content.dhg.myharmony.com/1.0"/>
  </object>
</body>
</html>
"""


@router.get("/MartiniWeb/account/harmonyconfig")
async def harmony_config_page() -> Response:
    return Response(content=_HARMONY_CONFIG_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# RRT stub
# ---------------------------------------------------------------------------

@router.get("/remoterecoverytool/DefaultRRT.html")
async def rrt_stub() -> Response:
    html = "<html><body><h1>Remote Recovery Tool</h1><p>Not supported in Harmony Revival.</p></body></html>"
    return Response(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# Auth proxy  (Logi ID via martiniweb shim)
# ---------------------------------------------------------------------------

_LOGI_BASE = "https://accounts.logi.com"
_SETUP_BASE = "https://setup.myharmony.com"


async def _proxy_post(url: str, body: bytes, headers: dict) -> JSONResponse:
    """Thin HTTPS proxy for Logi ID auth calls."""
    proxy_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in {"host", "content-length", "transfer-encoding"}
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, content=body, headers=proxy_headers)
        return JSONResponse(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text},
                            status_code=resp.status_code)
    except httpx.RequestError as exc:
        log.warning("Auth proxy error for %s: %s", url, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Auth upstream error: {exc}") from exc


@router.post("/martiniweb/account/ProceedWithLIPLogin")
async def proceed_with_lip_login(request: Request) -> JSONResponse:
    """Proxy Logi ID OIDC sign-in (step 1 of Logitech auth chain)."""
    body = await request.body()
    target = f"{_SETUP_BASE}/martiniweb/account/ProceedWithLIPLogin?{request.url.query}"
    return await _proxy_post(target, body, dict(request.headers))


@router.post("/martiniweb/account/Refresh_RefreshToken")
async def refresh_token(request: Request) -> JSONResponse:
    body = await request.body()
    target = f"{_SETUP_BASE}/martiniweb/account/Refresh_RefreshToken?{request.url.query}"
    return await _proxy_post(target, body, dict(request.headers))


@router.get("/martiniweb/account/GetLIPUserInfo")
async def get_lip_user_info(request: Request) -> JSONResponse:
    aid = request.query_params.get("aId", "")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_SETUP_BASE}/martiniweb/account/GetLIPUserInfo",
                params={"aId": aid},
            )
        return JSONResponse(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text},
                            status_code=resp.status_code)
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ---------------------------------------------------------------------------
# Compatibility endpoint: Security.svc sign-in  (step 2 of Logitech auth chain)
# Proxied to Logitech's real SOAP endpoint, or handled natively when standalone.
# ---------------------------------------------------------------------------

@router.post("/CompositeSecurityServices/Security.svc/json2/signin")
@router.post("/CompositeSecurityServices/Security.svc/json2/SignIn")
async def security_signin(request: Request) -> JSONResponse:
    """
    Logitech auth chain step 2: exchange Logi ID access_token for Harmony AuthToken.

    In proxy mode: forward to real svcs.myharmony.com.
    In standalone mode: generate our own JWT (TODO: wire to our own auth).

    # TODO: implement standalone mode — issue our own token from /api/auth/login JWT
    """
    body = await request.body()
    target = "https://svcs.myharmony.com/CompositeSecurityServices/Security.svc/json2/signin"
    return await _proxy_post(target, body, dict(request.headers))


@router.post("/CompositeSecurityServices/Security.svc/json2/GetAuthenticatedUserAuthToken")
async def get_authenticated_user_auth_token(request: Request) -> JSONResponse:
    """
    Logitech auth chain step 3: get hub-specific cloud token.

    # TODO: standalone mode — derive hub token from our own JWT
    """
    body = await request.body()
    target = "https://svcs.myharmony.com/CompositeSecurityServices/Security.svc/json2/GetAuthenticatedUserAuthToken"
    return await _proxy_post(target, body, dict(request.headers))
