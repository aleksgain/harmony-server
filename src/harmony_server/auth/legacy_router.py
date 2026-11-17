"""Logitech's three-step sign-in chain, re-implemented against our own user store.

From MSI / decompiled-client forensics:

1. ``POST https://accounts.logi.com/identity/signin`` ``{email, password}``
   -> OIDC-style ``id_token``/``access_token``/``refresh_token``.
2. ``POST https://svcs.myharmony.com/CompositeSecurityServices/Security.svc/json2/signin``
   ``{UserAuthToken: <access_token>}`` -> ``AuthToken`` + ``harmonyauth`` cookie.
3. ``POST .../Security.svc/json2/GetAuthenticatedUserAuthToken`` with
   ``Authorization: <AuthToken>`` -> the token written into the hub's
   ``provisionInfo.authToken``.

Endpoint paths and the token hand-offs are known; exact request/response field casing
beyond the names above is a best guess (WCF json2 is usually PascalCase) - captures welcome.
Our tokens are our own JWTs; no Logitech client ids or keys are used.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from harmony_server.auth.router import authenticate
from harmony_server.auth.security import (
    SCOPE_HARMONY,
    SCOPE_HUB,
    SCOPE_LOGI,
    SCOPE_USER,
    decode_token,
    issue_token,
)
from harmony_server.deps import SessionDep, SettingsDep

SEC = "/CompositeSecurityServices/Security.svc/json2"
COOKIE = "harmonyauth"


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    form_ct = request.headers.get("content-type", "")
    if "form" in form_ct:
        return dict((await request.form()).items())
    return {}


def _get(d: dict[str, Any], *keys: str) -> str | None:
    lowered = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v:
            return str(v)
    return None


def build_logi_router() -> APIRouter:
    """Step 1 - mounted on the ``logi`` vhost (accounts.logi.com)."""
    router = APIRouter(tags=["legacy: auth chain"])

    def _oidc(account_id: int, settings: SettingsDep) -> dict[str, Any]:
        access = issue_token(account_id, settings, scope=SCOPE_LOGI)
        return {
            "access_token": access,
            "id_token": access,
            "refresh_token": issue_token(account_id, settings, scope="refresh"),
            "token_type": "Bearer",
            "expires_in": settings.jwt_ttl_minutes * 60,
        }

    @router.post("/identity/signin")
    async def signin(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        body = await _body(request)
        email, password = _get(body, "email", "username"), _get(body, "password")
        account = await authenticate(session, email or "", password or "")
        if account is None or account.id is None:
            return JSONResponse({"error": "invalid_grant"}, 401)
        return JSONResponse(_oidc(account.id, settings))

    @router.post("/identity/oauth2/token")
    async def token(request: Request, session: SessionDep, settings: SettingsDep) -> JSONResponse:
        body = await _body(request)
        grant = _get(body, "grant_type")
        if grant == "refresh_token":
            aid = decode_token(_get(body, "refresh_token") or "", settings, ("refresh",))
        elif grant == "password":
            acct = await authenticate(
                session, _get(body, "username", "email") or "", _get(body, "password") or ""
            )
            aid = acct.id if acct else None
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, 400)
        if aid is None:
            return JSONResponse({"error": "invalid_grant"}, 400)
        return JSONResponse(_oidc(aid, settings))

    return router


def build_security_router() -> APIRouter:
    """Steps 2 and 3 - mounted on the ``svcs`` vhost."""
    router = APIRouter(tags=["legacy: auth chain"])

    @router.post(f"{SEC}/signin")
    @router.post(f"{SEC}/LoginUser")
    async def harmony_signin(
        request: Request, session: SessionDep, settings: SettingsDep
    ) -> JSONResponse:
        body = await _body(request)
        aid = decode_token(
            _get(body, "UserAuthToken", "userAuthToken", "access_token", "token") or "",
            settings, (SCOPE_LOGI, SCOPE_USER),
        )
        if aid is None and _get(body, "email", "username"):
            # Older clients send credentials directly.
            acct = await authenticate(
                session, _get(body, "email", "username") or "", _get(body, "password") or ""
            )
            aid = acct.id if acct else None
        if aid is None:
            return JSONResponse({"ErrorCode": 401, "Message": "Invalid credentials"}, 401)
        auth = issue_token(aid, settings, scope=SCOPE_HARMONY)
        resp = JSONResponse({"AuthToken": auth, "AccountId": aid, "Account": {"Id": aid}})
        resp.set_cookie(COOKIE, auth, httponly=True, secure=True, samesite="none")
        return resp

    @router.post(f"{SEC}/GetAuthenticatedUserAuthToken")
    @router.post(f"{SEC}/GetUserAuthToken")
    async def hub_token(request: Request, settings: SettingsDep) -> JSONResponse:
        raw = request.headers.get("authorization") or request.cookies.get(COOKIE) or ""
        aid = decode_token(raw, settings, (SCOPE_HARMONY, SCOPE_USER))
        if aid is None:
            return JSONResponse({"ErrorCode": 401, "Message": "Not signed in"}, 401)
        body = await _body(request)
        wanted = _get(body, "accountId", "AccountId")
        if wanted and wanted != str(aid):
            return JSONResponse({"ErrorCode": 403, "Message": "accountId mismatch"}, 403)
        return JSONResponse(
            {"UserAuthToken": issue_token(aid, settings, scope=SCOPE_HUB), "AccountId": aid}
        )

    @router.post(f"{SEC}/Ping")
    @router.get(f"{SEC}/Ping")
    async def ping() -> JSONResponse:
        return JSONResponse("pong")

    return router
