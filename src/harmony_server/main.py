"""Harmony Revival — self-hosted MyHarmony cloud backend.

Start with:
    harmony-server          # uses config from .env / environment
    uvicorn harmony_server.main:app --host 0.0.0.0 --port 8080

The server answers for all Logitech hostnames via vhost routing middleware so
that a DNS-rewrite deployment (*.myharmony.com → this server) works without any
client changes.  It also exposes a harmony-revival-native REST API under /api/
used by the new Electron client.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from harmony_server.config import Settings, get_settings
from harmony_server.db.database import Database
from harmony_server.device_db.store import DeviceDB
from harmony_server.middleware.capture import CaptureUnknownMiddleware
from harmony_server.middleware.hostname_routing import HostnameRoutingMiddleware

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    settings: Settings = app.state.settings

    # -- Database ----------------------------------------------------------------
    db = Database(settings.resolved_db_path)
    await db.init()
    app.state.db = db

    # -- Device DB ---------------------------------------------------------------
    device_db = DeviceDB(settings.device_db_path)
    await device_db.init()
    app.state.device_db = device_db

    log.info("Harmony server ready on http://%s:%d", settings.bind_host, settings.http_port)
    yield

    # -- Cleanup -----------------------------------------------------------------
    await db.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Harmony Revival Server",
        description="Self-hosted replacement for Logitech's MyHarmony cloud (svcs/setup.myharmony.com).",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    # -- CORS (Electron renderer hits /api/* directly) ---------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Request capture (record unknown endpoints for reverse engineering) -------
    if settings.capture_unknown_requests:
        app.add_middleware(
            CaptureUnknownMiddleware,
            capture_dir=settings.resolved_capture_dir,
            redact_headers=set(settings.capture_redact_headers),
        )

    # -- Vhost routing (must be outermost) ---------------------------------------
    app.add_middleware(HostnameRoutingMiddleware, vhosts=settings.vhosts)

    # -- Routers -----------------------------------------------------------------
    _mount_routers(app)

    # -- Generic 404 for Logitech endpoints we haven't implemented yet -----------
    @app.exception_handler(404)
    async def not_found(_request: Request, _exc: Any) -> JSONResponse:
        return JSONResponse(
            {"errorCode": "404", "errorMessage": "Not implemented in Harmony Revival"},
            status_code=404,
        )

    return app


def _mount_routers(app: FastAPI) -> None:
    from harmony_server.auth.router import router as auth_router
    from harmony_server.device_db.router import router as device_db_router
    from harmony_server.discovery.router import router as discovery_router
    from harmony_server.firmware.router import router as firmware_router
    from harmony_server.hub.router import router as hub_router
    from harmony_server.provisioning.router import router as provisioning_router
    from harmony_server.telemetry.router import router as telemetry_router
    from harmony_server.web.router import router as web_router

    # Harmony Revival native REST API (new Electron client)
    app.include_router(auth_router)
    app.include_router(hub_router)
    app.include_router(device_db_router)

    # Logitech-compatible endpoints (legacy Silverlight client via DNS rewrite)
    app.include_router(provisioning_router)
    app.include_router(discovery_router)
    app.include_router(firmware_router)
    app.include_router(telemetry_router)
    app.include_router(web_router)


# Singleton app for uvicorn / ASGI servers
app = create_app()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command()
@click.option("--host", default=None, help="Override bind host (HARMONY_BIND_HOST)")
@click.option("--port", default=None, type=int, help="Override HTTP port (HARMONY_HTTP_PORT)")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload (dev only)")
@click.option("--log-level", default="info", type=click.Choice(["debug", "info", "warning", "error"]))
def cli(host: str | None, port: int | None, reload: bool, log_level: str) -> None:
    """Start the Harmony Revival server."""
    settings = get_settings()

    # Env overrides from CLI flags
    if host:
        os.environ["HARMONY_BIND_HOST"] = host
    if port:
        os.environ["HARMONY_HTTP_PORT"] = str(port)

    effective_host = host or settings.bind_host
    effective_port = port or settings.http_port

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    log.info("Starting Harmony Revival server %s:%d", effective_host, effective_port)

    uvicorn.run(
        "harmony_server.main:app",
        host=effective_host,
        port=effective_port,
        reload=reload,
        log_level=log_level,
        # In production add ssl_keyfile/ssl_certfile for TLS
    )


if __name__ == "__main__":
    cli()
