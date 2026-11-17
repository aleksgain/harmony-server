"""App factory, lifespan and the ``harmony-server`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from harmony_server import __version__
from harmony_server.auth import legacy_router as auth_legacy
from harmony_server.auth.router import router as auth_router
from harmony_server.config import Settings, get_settings
from harmony_server.db import Database
from harmony_server.device_db.router import router as device_db_router
from harmony_server.device_db.store import DeviceDB
from harmony_server.discovery import router as discovery
from harmony_server.firmware import router as firmware
from harmony_server.hub import legacy_router as hub_legacy
from harmony_server.hub.router import router as hub_router
from harmony_server.middleware.hostname_routing import VHOST_PREFIX, HostnameRoutingMiddleware
from harmony_server.middleware.proxy import ProxyMiddleware
from harmony_server.middleware.tls_intercept import ensure_certificates
from harmony_server.provisioning import router as provisioning
from harmony_server.provisioning.hub_registry import HubRegistry
from harmony_server.telemetry.router import router as telemetry_router
from harmony_server.web import router as web

log = logging.getLogger("harmony_server")


def _vhost(name: str, *routers: APIRouter) -> APIRouter:
    r = APIRouter(prefix=f"{VHOST_PREFIX}/{name}")
    for sub in routers:
        r.include_router(sub)
    return r


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.resolved_db_path)
        await db.init()
        store = DeviceDB(settings.device_db_path)
        await store.load()
        app.state.db = db
        app.state.device_db = store
        if settings.secret_key == "change-me-in-production":
            log.warning("HARMONY_SECRET_KEY is the default - set a real secret!")
        log.info("harmony-server %s started in %s mode", __version__, settings.server_mode)
        try:
            yield
        finally:
            await db.dispose()

    app = FastAPI(
        title="harmony-server",
        version=__version__,
        description="Self-hosted MyHarmony cloud replacement (Harmony Revival).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.registry = HubRegistry()

    # New-client API (all hosts)
    app.include_router(auth_router)
    app.include_router(hub_router)
    app.include_router(device_db_router)
    app.include_router(firmware.api_router)
    app.include_router(web.root_router)

    # Logitech vhosts
    hbus = provisioning.build_hbus_router()
    app.include_router(
        _vhost(
            "svcs",
            hbus,
            provisioning.build_wcf_router(),
            discovery.build_router(),
            auth_legacy.build_security_router(),
            hub_legacy.build_router(),
        )
    )
    app.include_router(_vhost("setup", hbus))
    app.include_router(_vhost("sl", web.sl_router))
    app.include_router(_vhost("content", web.content_router))
    app.include_router(_vhost("sus", firmware.vhost_router))
    app.include_router(_vhost("telemetry", telemetry_router))
    app.include_router(_vhost("logi", auth_legacy.build_logi_router()))
    app.include_router(web.fallback_router)  # must be last

    app.mount(
        "/device-db",
        StaticFiles(directory=settings.device_db_path, html=True, check_dir=False),
        name="device-db",
    )

    app.add_middleware(HostnameRoutingMiddleware, vhosts=settings.vhosts)
    if settings.server_mode == "proxy":
        # Outermost: relays Logitech hosts before vhost routing sees them.
        app.add_middleware(ProxyMiddleware, settings=settings)
    return app


async def _serve(settings: Settings, log_level: str) -> None:
    app = create_app(settings)
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.bind_host,
                port=settings.http_port,
                log_level=log_level,
                proxy_headers=False,
                lifespan="on",
            )
        )
    ]
    if settings.enable_tls:
        paths = ensure_certificates(
            settings.resolved_cert_dir, settings.all_hostnames() + settings.cert_extra_names
        )
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=settings.bind_host,
                    port=settings.https_port,
                    log_level=log_level,
                    ssl_certfile=str(paths.server_cert),
                    ssl_keyfile=str(paths.server_key),
                    # Lifespan runs once (on the HTTP server); both share app.state.
                    lifespan="off",
                )
            )
        )
        log.info("TLS on :%d - trust %s on clients", settings.https_port, paths.ca_cert)
    await asyncio.gather(*(s.serve() for s in servers))


def cli() -> None:
    parser = argparse.ArgumentParser(prog="harmony-server", description=__doc__)
    parser.add_argument("--host", help="bind address")
    parser.add_argument("--http-port", type=int)
    parser.add_argument("--https-port", type=int)
    parser.add_argument("--no-tls", action="store_true", help="HTTP only")
    parser.add_argument("--mode", choices=["standalone", "proxy"])
    parser.add_argument("--gen-certs", action="store_true", help="(re)generate certs and exit")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    overrides: dict[str, Any] = {}
    if args.host:
        overrides["bind_host"] = args.host
    if args.http_port:
        overrides["http_port"] = args.http_port
    if args.https_port:
        overrides["https_port"] = args.https_port
    if args.no_tls:
        overrides["enable_tls"] = False
    if args.mode:
        overrides["server_mode"] = args.mode
    settings = Settings(**overrides) if overrides else get_settings()

    if args.gen_certs:
        paths = ensure_certificates(
            settings.resolved_cert_dir,
            settings.all_hostnames() + settings.cert_extra_names,
            force=True,
        )
        print(f"CA: {paths.ca_cert}\nServer cert: {paths.server_cert}")
        return
    asyncio.run(_serve(settings, args.log_level))


if __name__ == "__main__":
    cli()
