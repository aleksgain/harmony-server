"""Hub <-> account mapping.

Persistent state lives in SQLite (``Hub`` table). A small in-memory map tracks volatile
presence (last request, reported versions) so the status API does not hammer the DB on
every hub poll.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from harmony_server.config import Settings
from harmony_server.db import Account, Hub, utcnow
from harmony_server.provisioning.models import HubOut, ProvisionData

CLOUD_DOMAIN = "svcs.myharmony.com"
HUB_ID_KEYS = ("hubId", "remoteId", "globalRemoteId", "activeRemoteId", "RemoteId", "HubId")
UUID_KEYS = ("uuid", "hubUuid", "serialNumber", "SerialNumber", "unitCertificate", "guid")


@dataclass
class Presence:
    last_seen: datetime
    ip: str | None
    reported: dict[str, Any] = field(default_factory=dict)


class HubRegistry:
    def __init__(self) -> None:
        self.presence: dict[int, Presence] = {}

    # ---------------------------------------------------------------- identity helpers
    @staticmethod
    def cloud_auth(hub: Hub) -> str:
        """``<domain>;<accountId>;<globalRemoteId>;<token>`` as stored on the hub."""
        account = "" if hub.account_id is None else str(hub.account_id)
        return f"{CLOUD_DOMAIN};{account};{hub.global_remote_id};{hub.cloud_token}"

    @staticmethod
    def parse_cloud_auth(value: str) -> tuple[str | None, str | None, str | None]:
        """Return (accountId, globalRemoteId, token) from a cloudAuth blob or bare token."""
        value = value.removeprefix("Bearer ").strip()
        parts = value.split(";")
        if len(parts) == 4:
            return parts[1] or None, parts[2] or None, parts[3] or None
        return None, None, value or None

    async def _allocate_remote_id(self, session: AsyncSession) -> int:
        while True:
            candidate = secrets.randbelow(89_999_999) + 10_000_000
            if (
                await session.exec(select(Hub).where(Hub.global_remote_id == candidate))
            ).first() is None:
                return candidate

    # ---------------------------------------------------------------- lookups
    async def get_by_remote_id(self, session: AsyncSession, remote_id: str | int) -> Hub | None:
        try:
            rid = int(str(remote_id).strip())
        except ValueError:
            return None
        return (await session.exec(select(Hub).where(Hub.global_remote_id == rid))).first()

    async def get_by_uuid(self, session: AsyncSession, uuid: str) -> Hub | None:
        return (await session.exec(select(Hub).where(Hub.hub_uuid == uuid))).first()

    async def get_by_token(self, session: AsyncSession, token: str) -> Hub | None:
        return (await session.exec(select(Hub).where(Hub.cloud_token == token))).first()

    async def identify(
        self,
        session: AsyncSession,
        *,
        params: dict[str, Any],
        auth_header: str | None,
        client_ip: str | None,
        explicit_hub_id: str | None = None,
    ) -> Hub | None:
        """Best-effort identification of the calling hub.

        Order: explicit hub id -> id keys in params -> cloudAuth/bearer token -> uuid keys
        -> unique IP match. We have no capture of what a real hub sends to the cloud, so
        every plausible carrier is checked.
        """
        candidates: list[str] = []
        if explicit_hub_id:
            candidates.append(explicit_hub_id)
        candidates += [str(params[k]) for k in HUB_ID_KEYS if params.get(k) not in (None, "")]
        for c in candidates:
            hub = await self.get_by_remote_id(session, c)
            if hub:
                return hub

        if auth_header:
            _acct, rid, token = self.parse_cloud_auth(auth_header)
            if token:
                hub = await self.get_by_token(session, token)
                if hub:
                    return hub
            if rid:
                hub = await self.get_by_remote_id(session, rid)
                if hub:
                    return hub

        for k in UUID_KEYS:
            if params.get(k):
                hub = await self.get_by_uuid(session, str(params[k]))
                if hub:
                    return hub

        if client_ip:
            matches = (await session.exec(select(Hub).where(Hub.ip_address == client_ip))).all()
            if len(matches) == 1:
                return matches[0]
        return None

    # ---------------------------------------------------------------- mutations
    async def register(
        self,
        session: AsyncSession,
        *,
        hub_uuid: str | None = None,
        ip: str | None = None,
        name: str | None = None,
        sw_version: str | None = None,
        account_id: int | None = None,
        global_remote_id: int | None = None,
    ) -> Hub:
        hub = Hub(
            account_id=account_id,
            friendly_name=name or "Harmony Hub",
            global_remote_id=global_remote_id or await self._allocate_remote_id(session),
            hub_uuid=hub_uuid,
            cloud_token=secrets.token_urlsafe(32),
            hub_sw_version=sw_version,
            ip_address=ip,
            last_seen=utcnow() if ip else None,
        )
        session.add(hub)
        await session.commit()
        await session.refresh(hub)
        return hub

    async def claim(
        self, session: AsyncSession, hub: Hub, account: Account, name: str | None = None
    ) -> Hub:
        if hub.account_id is not None and hub.account_id != account.id:
            raise PermissionError("Hub is already claimed by another account")
        hub.account_id = account.id
        if name:
            hub.friendly_name = name
        session.add(hub)
        await session.commit()
        await session.refresh(hub)
        return hub

    async def touch(
        self, session: AsyncSession, hub: Hub, ip: str | None, reported: dict[str, Any]
    ) -> None:
        now = utcnow()
        assert hub.id is not None
        self.presence[hub.id] = Presence(last_seen=now, ip=ip, reported=reported)
        changed = False
        if ip and hub.ip_address != ip:
            hub.ip_address, changed = ip, True
        sw = reported.get("hubSwVersion") or reported.get("fwVersion")
        if sw and hub.hub_sw_version != str(sw):
            hub.hub_sw_version, changed = str(sw), True
        # Persist last_seen at most once a minute.
        if changed or hub.last_seen is None or (now - hub.last_seen).total_seconds() > 60:
            hub.last_seen = now
            session.add(hub)
            await session.commit()

    async def list_for_account(self, session: AsyncSession, account_id: int) -> list[Hub]:
        return list((await session.exec(select(Hub).where(Hub.account_id == account_id))).all())

    # ---------------------------------------------------------------- views
    async def provision_data(
        self, session: AsyncSession, hub: Hub, settings: Settings
    ) -> ProvisionData:
        email = None
        if hub.account_id is not None:
            acct = await session.get(Account, hub.account_id)
            email = acct.email if acct else None
        rid = str(hub.global_remote_id)
        return ProvisionData(
            accountId=None if hub.account_id is None else str(hub.account_id),
            activeRemoteId=rid,
            globalRemoteId=rid,
            discoveryServer=settings.provision_discovery_server,
            discoveryServerCF=settings.content_server_url,
            productId=hub.product_id,
            mode=hub.mode,
            email=email,
            username=email,
            cloudAuth=self.cloud_auth(hub),
            token=hub.cloud_token,
        )

    def hub_out(self, hub: Hub, include_secret: bool = False) -> HubOut:
        return HubOut(
            id=hub.id or 0,
            globalRemoteId=str(hub.global_remote_id),
            hubUuid=hub.hub_uuid,
            friendlyName=hub.friendly_name,
            accountId=None if hub.account_id is None else str(hub.account_id),
            ipAddress=hub.ip_address,
            hubSwVersion=hub.hub_sw_version,
            lastSeen=hub.last_seen.isoformat() if hub.last_seen else None,
            syncedConfigVersion=hub.synced_config_version,
            cloudAuth=self.cloud_auth(hub) if include_secret else None,
        )
