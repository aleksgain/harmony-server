"""``SERVER_MODE=proxy``: relay Logitech-host traffic to the real cloud and record it.

While Logitech's servers still answer, this is the fastest way to fill every "unknown
schema" in this project: point a hub (or the legacy app) at this server via the DNS
override, run in proxy mode, and every request/response pair lands in
``<capture_dir>/proxy-<vhost>.jsonl``.

The loop problem: under the DNS override, ``svcs.myharmony.com`` resolves to *us*. Give
the real upstream addresses in ``HARMONY_PROXY_UPSTREAM_IPS`` (JSON map host->IP), or run
the server on a machine that does not use the overriding resolver. We connect to the IP
but send the original hostname as SNI and verify the certificate against it.

Caveat: the hub must accept *our* certificate for this to see hub traffic (see
docs/tls-setup.md). For the desktop app, trusting our CA is enough.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os
import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from harmony_server.config import Settings
from harmony_server.middleware.hostname_routing import SHARED_PREFIXES, normalise_host

log = logging.getLogger(__name__)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}
MAX_CAPTURE = 2 * 1024 * 1024


def _encode_body(data: bytes) -> dict[str, Any]:
    clipped = data[:MAX_CAPTURE]
    try:
        return {"text": clipped.decode("utf-8"), "truncated": len(data) > MAX_CAPTURE}
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(clipped).decode(), "truncated": len(data) > MAX_CAPTURE}


class ProxyMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._by_host = {h.lower(): v for v, hosts in settings.vhosts.items() for h in hosts}
        self._client = httpx.AsyncClient(
            timeout=settings.proxy_timeout_seconds,
            verify=settings.proxy_verify_tls,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = normalise_host(headers.get("host"))
        vhost = self._by_host.get(host)
        if vhost is None or scope["path"].startswith(SHARED_PREFIXES):
            await self.app(scope, receive, send)
            return
        await self._relay(scope, receive, send, host, vhost, headers)

    async def _relay(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        host: str,
        vhost: str,
        headers: dict[str, str],
    ) -> None:
        body = b""
        while True:
            msg: Message = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        path = scope.get("raw_path") or scope["path"].encode()
        target = path.decode("latin-1")
        if scope.get("query_string"):
            target += "?" + scope["query_string"].decode("latin-1")
        ip = self.settings.proxy_upstream_ips.get(host)
        url = f"https://{ip or host}{target}"
        fwd = {k: v for k, v in headers.items() if k not in HOP_BY_HOP}
        fwd["host"] = host
        started = time.monotonic()
        try:
            resp = await self._client.request(
                scope["method"],
                url,
                headers=fwd,
                content=body,
                extensions={"sni_hostname": host} if ip else None,
            )
            status, resp_headers, resp_body = resp.status_code, resp.headers, resp.content
            error = None
        except httpx.HTTPError as exc:
            status, resp_headers, resp_body, error = (
                502,
                httpx.Headers(),
                b"upstream error",
                str(exc),
            )
            log.warning("Proxy %s %s failed: %s", scope["method"], url, exc)
        await self._record(
            vhost,
            host,
            scope,
            target,
            headers,
            body,
            status,
            resp_headers,
            resp_body,
            error,
            time.monotonic() - started,
        )
        out_headers = [
            (k.encode(), v.encode()) for k, v in resp_headers.items() if k.lower() not in HOP_BY_HOP
        ]
        out_headers.append((b"content-length", str(len(resp_body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": out_headers})
        await send({"type": "http.response.body", "body": resp_body})

    async def _record(
        self,
        vhost: str,
        host: str,
        scope: Scope,
        target: str,
        req_headers: dict[str, str],
        req_body: bytes,
        status: int,
        resp_headers: httpx.Headers,
        resp_body: bytes,
        error: str | None,
        elapsed: float,
    ) -> None:
        redact = {h.lower() for h in self.settings.capture_redact_headers}

        def clean(h: dict[str, str]) -> dict[str, str]:
            return {k: ("<redacted>" if k.lower() in redact else v) for k, v in h.items()}

        record = {
            "ts": datetime.now(UTC).isoformat(),
            "client": (scope.get("client") or [None])[0],
            "request": {
                "method": scope["method"],
                "host": host,
                "target": target,
                "headers": clean(req_headers),
                "body": _encode_body(req_body),
            },
            "response": {
                "status": status,
                "headers": clean(dict(resp_headers)),
                "body": _encode_body(resp_body),
                "error": error,
            },
            "elapsedMs": round(elapsed * 1000),
        }
        cap_dir: Path = self.settings.resolved_capture_dir
        await aiofiles.os.makedirs(cap_dir, exist_ok=True)
        async with aiofiles.open(cap_dir / f"proxy-{vhost}.jsonl", "a", encoding="utf-8") as fh:
            await fh.write(json.dumps(record) + "\n")
