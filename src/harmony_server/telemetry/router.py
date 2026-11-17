"""Sink for hub/app telemetry hosts (logging.dhg, home.myharmony.com).

Answering 200 stops clients retrying; nothing is forwarded anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["legacy: telemetry"])


@router.post("/1.0/logger/submitHubLog")
@router.post("/1.0/logger/submitUsageData")
async def submit_log(request: Request) -> dict[str, Any]:
    await request.body()
    return {"status": "ok"}


@router.get("/time/locale")
async def time_locale() -> dict[str, Any]:
    now = datetime.now(UTC)
    return {"utc": now.isoformat(), "epochMs": int(now.timestamp() * 1000), "offsetMinutes": 0}
