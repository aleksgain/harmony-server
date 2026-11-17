"""Request capture for reverse engineering.

Anything a hub or the legacy app sends that we do not implement is appended to
``<capture_dir>/<vhost>.jsonl``. These files are what fills the Discovery/config-sync gap:
please attach them (after review) to issues.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import Request

MAX_BODY = 1024 * 1024


async def capture_request(
    request: Request, capture_dir: Path, redact: list[str], note: str = ""
) -> Path:
    body = await request.body()
    vhost = getattr(request.state, "vhost", None) or "default"
    redact_l = {h.lower() for h in redact}
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "note": note,
        "client": request.client.host if request.client else None,
        "method": request.method,
        "host": request.headers.get("host"),
        "path": request.url.path.split("/", 3)[-1] if vhost != "default" else request.url.path,
        "url": str(request.url),
        "query": request.url.query,
        "headers": {
            k: ("<redacted>" if k.lower() in redact_l else v) for k, v in request.headers.items()
        },
        "body": body[:MAX_BODY].decode("utf-8", errors="replace"),
        "body_truncated": len(body) > MAX_BODY,
    }
    await aiofiles.os.makedirs(capture_dir, exist_ok=True)
    path = capture_dir / f"{vhost}.jsonl"
    async with aiofiles.open(path, "a", encoding="utf-8") as fh:
        await fh.write(json.dumps(record) + "\n")
    return path
