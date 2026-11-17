"""Thin aioharmony wrapper for live hub reads/commands over the LAN (8088/5222).

Connections are short-lived (connect -> act -> close): the server is not a long-running
hub client, and holding sockets open would compete with the user's own apps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aioharmony.const import SendCommandDevice
from aioharmony.harmonyapi import HarmonyAPI

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15.0


class HubUnreachable(RuntimeError):
    pass


@asynccontextmanager
async def connect(ip: str, timeout: float = CONNECT_TIMEOUT) -> AsyncIterator[HarmonyAPI]:
    api = HarmonyAPI(ip_address=ip)
    try:
        ok = await asyncio.wait_for(api.connect(), timeout)
    except (TimeoutError, OSError) as exc:
        raise HubUnreachable(f"Cannot connect to hub at {ip}: {exc}") from exc
    if not ok:
        raise HubUnreachable(f"Hub at {ip} refused connection")
    try:
        yield api
    finally:
        try:
            await asyncio.wait_for(api.close(), 5)
        except (TimeoutError, OSError):
            log.debug("Error closing hub connection to %s", ip, exc_info=True)


async def status(ip: str) -> dict[str, Any]:
    async with connect(ip) as api:
        activity_id, activity_name = api.current_activity
        return {
            "currentActivityId": None if activity_id is None else str(activity_id),
            "currentActivityLabel": activity_name,
            "hubConfigVersion": api.hub_config.config_version,
            "hubSwVersion": api.fw_version,
            "hubId": api.hub_id,
            "accountId": api.account_id,
        }


async def read_config(ip: str) -> dict[str, Any]:
    """The hub's own engine?config - the shape our bundle mirrors."""
    async with connect(ip) as api:
        return dict(api.config)


async def start_activity(ip: str, activity_id: str) -> tuple[bool, str | None]:
    async with connect(ip) as api:
        if activity_id == "-1":
            return await api.power_off(), None
        ok, msg = await api.start_activity(activity_id)
        return bool(ok), msg


async def send_command(ip: str, device_id: str, command: str, delay: float, repeat: int) -> list[str]:
    async with connect(ip) as api:
        cmds: list[Any] = [
            SendCommandDevice(device=int(device_id), command=command, delay=delay)
            for _ in range(repeat)
        ]
        errors = await api.send_commands(cmds)
        return [f"{e.command.command}: {e.code} {e.msg}" for e in errors or []]


async def sync(ip: str) -> bool:
    """Ask the hub to re-sync (``setup.sync``) - after we publish a new configVersion."""
    async with connect(ip) as api:
        return bool(await api.sync())
