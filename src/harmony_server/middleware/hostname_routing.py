"""Virtual-host dispatch on the ``Host`` header.

All Logitech hostnames resolve (via local DNS override) to this one server. This ASGI
middleware maps the request's host to a *vhost* name and rewrites the path to
``/_vh/<vhost><original-path>`` so that each vhost's router can be mounted under its own
internal prefix and declare routes exactly as the real host exposed them (e.g. ``POST /``
on ``svcs.myharmony.com`` is provisioning, while ``POST /`` on any other host is not).

Paths under ``SHARED_PREFIXES`` (the new-client REST API, the device DB, health) are not
rewritten: they work on every hostname, including a bare IP or ``localhost``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

VHOST_PREFIX = "/_vh"
SHARED_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/device-db/",
    "/device-db",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def normalise_host(raw: str | None) -> str:
    """Lower-case and strip the port (handles ``[v6]:port`` too)."""
    if not raw:
        return ""
    host = raw.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


class HostnameRoutingMiddleware:
    def __init__(self, app: ASGIApp, vhosts: Mapping[str, Sequence[str]]) -> None:
        self.app = app
        self._by_host: dict[str, str] = {
            h.lower(): name for name, hosts in vhosts.items() for h in hosts
        }

    def vhost_for(self, host: str) -> str | None:
        return self._by_host.get(normalise_host(host))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")
        if path.startswith(VHOST_PREFIX + "/") or path == VHOST_PREFIX:
            # Internal namespace must never be addressed directly.
            await JSONResponse({"code": 404, "msg": "Not Found"}, status_code=404)(
                scope, receive, send
            )
            return

        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        vhost = self.vhost_for(host)
        state = scope.setdefault("state", {})
        state["vhost"] = vhost
        state["host"] = normalise_host(host)

        if vhost is not None and not path.startswith(SHARED_PREFIXES):
            new_path = f"{VHOST_PREFIX}/{vhost}{path}"
            scope = dict(scope)
            scope["path"] = new_path
            raw = scope.get("raw_path")
            if raw is not None:
                scope["raw_path"] = f"{VHOST_PREFIX}/{vhost}".encode() + raw
        await self.app(scope, receive, send)
