"""Device DB lookup API (thin wrapper over the static IR archive).

The archive files themselves are also served verbatim at ``/device-db/`` (mounted in
``main.py``), so clients can use either.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from harmony_server.deps import get_device_db
from harmony_server.device_db.store import DeviceDB, DeviceDBUnavailable

router = APIRouter(prefix="/api", tags=["device db"])
DBDep = Annotated[DeviceDB, Depends(get_device_db)]


def _guard(store: DeviceDB) -> None:
    if not store.available:
        raise HTTPException(503, "Device DB not installed (see device-db/README.md)")


@router.get("/devices/manifest")
async def manifest(store: DBDep) -> dict[str, Any]:
    _guard(store)
    return store.manifest


@router.get("/devices/")
async def search_manufacturers(
    store: DBDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 50,
) -> list[dict[str, Any]]:
    _guard(store)
    try:
        return store.search_manufacturers(q, limit)
    except DeviceDBUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/devices/{manufacturer}/")
async def list_models(
    manufacturer: str,
    store: DBDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100000)] = 1000,
) -> dict[str, Any]:
    _guard(store)
    entries = await store.models(manufacturer)
    if entries is None:
        raise HTTPException(404, "Unknown manufacturer")
    mfr = store.manufacturer(manufacturer)
    assert mfr is not None
    out = [
        {"model": (e.get("m") or "").strip() or None, "file": e["f"], "globalDeviceId": e.get("id")}
        for e in entries
    ]
    if q:
        needle = q.lower()
        out = [o for o in out if needle in (o["model"] or "").lower()]
    return {"manufacturer": mfr["n"], "slug": mfr["s"], "count": len(out), "models": out[:limit]}


@router.get("/devices/{manufacturer}/{model:path}")
async def get_device(
    manufacturer: str,
    model: str,
    store: DBDep,
    inline: Annotated[bool, Query(description="Inline the codeset's commands")] = True,
) -> dict[str, Any]:
    _guard(store)
    record = await store.device(manufacturer, model, inline_codeset=inline)
    if record is None:
        raise HTTPException(404, "Unknown manufacturer or model")
    return record


@router.get("/protocols/")
async def list_protocols(store: DBDep) -> list[dict[str, Any]]:
    _guard(store)
    return store.protocols


@router.get("/protocols/{name}")
async def get_protocol(name: str, store: DBDep) -> dict[str, Any]:
    _guard(store)
    data = await store.protocol(name)
    if data is None:
        raise HTTPException(404, "Unknown protocol")
    return data
