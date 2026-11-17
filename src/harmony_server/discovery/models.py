from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ServiceDescription(BaseModel):
    """``Logitech.Harmony.Services.Discovery.ServiceDescription`` data contract."""

    Address: str | None
    EnvironmentId: str | None
    Identifier: str | None
    Name: str | None


class ConfigSyncRequest(BaseModel):
    """Fields we try to read from a hub's config-sync request (names from state digest).

    TODO: validate against captured Discovery.svc packet.
    """

    model_config = ConfigDict(extra="allow")

    hubId: str | None = None
    configVersion: int | None = None
    contentVersion: int | None = None

    @classmethod
    def from_any(cls, body: dict[str, Any], query: dict[str, str]) -> ConfigSyncRequest:
        flat: dict[str, Any] = {}
        for src in (query, body, body.get("params") or {}, body.get("data") or {}):
            if isinstance(src, dict):
                flat.update(src)
        lower = {k.lower(): v for k, v in flat.items()}

        def pick(*keys: str) -> Any:
            for k in keys:
                if lower.get(k.lower()) not in (None, ""):
                    return lower[k.lower()]
            return None

        def as_int(v: Any) -> int | None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        return cls(
            hubId=None
            if (h := pick("hubId", "remoteId", "globalRemoteId", "activeRemoteId")) is None
            else str(h),
            configVersion=as_int(pick("configVersion")),
            contentVersion=as_int(pick("contentVersion")),
        )
