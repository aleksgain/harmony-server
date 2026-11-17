"""Placeholder web/static vhosts and the capture-everything fallback."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from harmony_server import __version__
from harmony_server.deps import SettingsDep
from harmony_server.middleware.capture import capture_request

PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>Harmony Revival</title>
<style>body{font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em}</style></head>
<body><h1>Harmony Revival</h1><p>harmony-server {version} is running.</p>
<p>The web client will be served from this host (<code>sl.dhg.myharmony.com</code>) once it
exists. API docs: <a href="/docs">/docs</a>. Device DB: <a href="/device-db/">/device-db/</a>.</p>
</body></html>"""

sl_router = APIRouter(tags=["web"])
content_router = APIRouter(tags=["content"])
root_router = APIRouter(tags=["meta"])
fallback_router = APIRouter(tags=["capture"])


@sl_router.get("/{path:path}", response_class=HTMLResponse)
async def spa(path: str) -> HTMLResponse:
    return HTMLResponse(PLACEHOLDER.replace("{version}", __version__))


@content_router.get("/")
async def content_root() -> RedirectResponse:
    return RedirectResponse("/device-db/")


@root_router.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(PLACEHOLDER.replace("{version}", __version__))


@root_router.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "mode": request.app.state.settings.server_mode,
        "deviceDb": request.app.state.device_db.available,
    }


@fallback_router.api_route(
    "/_vh/{vhost}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def capture_unknown(
    vhost: str, path: str, request: Request, settings: SettingsDep
) -> JSONResponse:
    """Anything a Logitech client asks for that we do not implement: log it and say so."""
    where = None
    if settings.capture_unknown_requests:
        where = await capture_request(
            request,
            settings.resolved_capture_dir,
            settings.capture_redact_headers,
            note="unimplemented",
        )
    return JSONResponse(
        {
            "code": 501,
            "msg": f"Not implemented by harmony-server: {vhost}/{path}",
            "captured": where is not None,
        },
        status_code=501,
    )
