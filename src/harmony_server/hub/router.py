"""``/api/hubs/*`` - REST API for the new cross-platform client."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from harmony_server.auth.security import SCOPE_HUB, issue_token
from harmony_server.db import Hub
from harmony_server.deps import AccountDep, SessionDep, SettingsDep, get_device_db
from harmony_server.device_db.store import DeviceDB
from harmony_server.discovery.config_compiler import latest_config, save_config, sync_decision
from harmony_server.hub.models import (
    ActivityAction,
    CommandRequest,
    HubConfigIn,
    HubStatus,
)
from harmony_server.local_control import hub_client
from harmony_server.provisioning.hub_registry import CLOUD_DOMAIN, HubRegistry
from harmony_server.provisioning.models import ClaimRequest, HubOut

router = APIRouter(prefix="/api/hubs", tags=["hubs"])


def _reg(request: Request) -> HubRegistry:
    reg: HubRegistry = request.app.state.registry
    return reg


async def owned_hub(hub_id: str, request: Request, session: SessionDep, account: AccountDep) -> Hub:
    """Resolve ``{id}`` (internal id or globalRemoteId) to a hub owned by the caller."""
    reg = _reg(request)
    hub = await reg.get_by_remote_id(session, hub_id)
    if hub is None and hub_id.isdigit():
        hub = await session.get(Hub, int(hub_id))
    if hub is None or hub.account_id != account.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hub not found")
    return hub


HubDep = Annotated[Hub, Depends(owned_hub)]


def _need_ip(hub: Hub) -> str:
    if not hub.ip_address:
        raise HTTPException(409, "Hub IP unknown - claim with ipAddress or let the hub check in")
    return hub.ip_address


@router.get("/", response_model=list[HubOut])
async def list_hubs(request: Request, session: SessionDep, account: AccountDep) -> list[HubOut]:
    assert account.id is not None
    reg = _reg(request)
    return [reg.hub_out(h) for h in await reg.list_for_account(session, account.id)]


@router.post("/claim", response_model=HubOut, status_code=201)
async def claim_hub(
    body: ClaimRequest, request: Request, session: SessionDep, account: AccountDep
) -> HubOut:
    """Claim a hub by globalRemoteId or uuid; creates the record if the hub is new.

    Creating lets a user pre-register a hub found on the LAN (aioharmony discovery) before
    it ever talks to the server.
    """
    reg = _reg(request)
    hub = await reg.get_by_remote_id(session, body.hubId) or await reg.get_by_uuid(session, body.hubId)
    if hub is None:
        hub = await reg.register(
            session, hub_uuid=None if body.hubId.isdigit() else body.hubId,
            global_remote_id=int(body.hubId) if body.hubId.isdigit() else None,
            ip=body.ipAddress, name=body.hubName,
        )
    try:
        hub = await reg.claim(session, hub, account, body.hubName)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.ipAddress and hub.ip_address != body.ipAddress:
        hub.ip_address = body.ipAddress
        session.add(hub)
        await session.commit()
    return reg.hub_out(hub, include_secret=True)


@router.get("/{hub_id}/provision-blob")
async def provision_blob(
    hub: HubDep, account: AccountDep, settings: SettingsDep,
    discovery_host: Annotated[str | None, Query(
        description="Override hostname (repointed/rooted hubs), e.g. harmony.lan")] = None,
) -> dict[str, Any]:
    """The ``account?provision`` payload to write into this hub.

    Write it over 8088/5222 (``vnd.logitech.setup/vnd.logitech.account?provision``) or the
    USB HID file RPC (``/sys/hlapi``). ``discoveryServer`` is the redirection lever.

    Note: the decompiled MyHarmony client writes the *full Discovery.svc URL* as
    ``discoveryServer``; other notes show a bare hostname. We emit the URL form (what the
    official client sent) and the bare host as ``domain``.
    """
    discovery = settings.discovery_server_url
    domain = CLOUD_DOMAIN
    if discovery_host:
        discovery = f"https://{discovery_host}/Discovery/Discovery.svc"
        domain = discovery_host
    assert account.id is not None
    info = {
        "domain": domain,
        "authToken": issue_token(account.id, settings, scope=SCOPE_HUB),
        "accountId": str(account.id),
        "globalRemoteId": str(hub.global_remote_id),
        "activeRemoteId": str(hub.global_remote_id),
        "discoveryServer": discovery,
        "username": account.email,
        "email": account.email,
        "language": "en-US",
        "mode": str(hub.mode),
        "name": hub.friendly_name,
        "susChannel": "production",
    }
    return {
        "hlapi": {"command": "account?provision", "params": {"provisionInfo": info}},
        "hbus": {"cmd": "vnd.logitech.setup/vnd.logitech.account?provision",
                 "params": {"provisionInfo": info}},
    }


@router.get("/{hub_id}/config")
async def get_config(hub: HubDep, session: SessionDep) -> dict[str, Any]:
    assert hub.id is not None
    latest = await latest_config(session, hub.id)
    if latest is None:
        return {"hubId": str(hub.global_remote_id), "configVersion": 0, "contentVersion": 0,
                "activity": [], "device": []}
    payload: dict[str, Any] = json.loads(latest.payload_json)
    return payload


@router.put("/{hub_id}/config")
async def put_config(
    hub: HubDep, body: HubConfigIn, session: SessionDep,
    device_db: Annotated[DeviceDB, Depends(get_device_db)],
    notify_hub: Annotated[bool, Query(description="Send setup.sync to the hub")] = False,
) -> dict[str, Any]:
    try:
        row = await save_config(session, hub, body, device_db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result: dict[str, Any] = {"configVersion": row.config_version,
                              "contentVersion": row.content_version, "hubNotified": False}
    if notify_hub and hub.ip_address:
        try:
            result["hubNotified"] = await hub_client.sync(hub.ip_address)
        except hub_client.HubUnreachable as exc:
            result["notifyError"] = str(exc)
    return result


@router.post("/{hub_id}/config/import-from-hub")
async def import_from_hub(hub: HubDep, session: SessionDep) -> dict[str, Any]:
    """Read the live hub's engine?config and store it as the server's config."""
    ip = _need_ip(hub)
    try:
        live = await hub_client.read_config(ip)
    except hub_client.HubUnreachable as exc:
        raise HTTPException(503, str(exc)) from exc
    row = await save_config(session, hub, HubConfigIn.model_validate(live), None)
    return {"configVersion": row.config_version, "contentVersion": row.content_version}


@router.get("/{hub_id}/status", response_model=HubStatus)
async def get_status(hub: HubDep, session: SessionDep) -> HubStatus:
    assert hub.id is not None
    latest = await latest_config(session, hub.id)
    st = HubStatus(
        hubId=str(hub.global_remote_id), online=False, ipAddress=hub.ip_address,
        serverConfigVersion=latest.config_version if latest else None,
        hubSwVersion=hub.hub_sw_version,
    )
    if not hub.ip_address:
        st.error = "Hub IP unknown"
        return st
    try:
        live = await hub_client.status(hub.ip_address)
    except hub_client.HubUnreachable as exc:
        st.error = str(exc)
        return st
    st.online = True
    st.currentActivityId = live["currentActivityId"]
    st.currentActivityLabel = live["currentActivityLabel"]
    st.hubConfigVersion = live["hubConfigVersion"]
    st.hubSwVersion = live["hubSwVersion"] or st.hubSwVersion
    st.syncRequired = sync_decision(latest, st.hubConfigVersion, None) == "update"
    return st


@router.post("/{hub_id}/activity")
async def activity(hub: HubDep, body: ActivityAction) -> dict[str, Any]:
    ip = _need_ip(hub)
    target = "-1" if body.action == "stop" else body.activityId
    try:
        ok, msg = await hub_client.start_activity(ip, target)
    except hub_client.HubUnreachable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": ok, "activityId": target, "message": msg}


@router.post("/{hub_id}/command")
async def command(hub: HubDep, body: CommandRequest) -> dict[str, Any]:
    ip = _need_ip(hub)
    try:
        errors = await hub_client.send_command(ip, body.deviceId, body.command, body.delay, body.repeat)
    except hub_client.HubUnreachable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, f"deviceId must be numeric: {exc}") from exc
    return {"ok": not errors, "errors": errors}


