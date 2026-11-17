"""Wire models for the Logitech-compatible provisioning surface."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote_plus

from pydantic import BaseModel, ConfigDict, Field


class HbusRequest(BaseModel):
    """Normalised ``{"id", "cmd", "params"}`` request.

    Accepts the 8088-HTTP shape ``{"id":1,"cmd":"...","params":{}}`` and the WebSocket
    envelope ``{"hubId":..,"hbus":{"cmd":..,"id":..,"params":{}}}``.
    """

    model_config = ConfigDict(extra="allow")

    id: Any = None
    cmd: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    hub_id: str | None = None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> HbusRequest:
        hbus = body.get("hbus")
        if isinstance(hbus, dict):
            inner = dict(hbus)
            return cls(
                id=inner.get("id"),
                cmd=str(inner.get("cmd", "")),
                params=inner.get("params") or {},
                hub_id=str(body["hubId"]) if body.get("hubId") is not None else None,
            )
        # aioharmony sends {"id ": 1, ...} (sic, trailing space) - tolerate it.
        req_id = body.get("id", body.get("id "))
        if "command" in body and "cmd" not in body:
            # /sys/hlapi (USB HID file RPC) shape: {"command": "account?provision", "params": {}}
            body = {**body, "cmd": body["command"]}
        params = body.get("params") or {}
        if not isinstance(params, dict):
            params = {"value": params}
        return cls(id=req_id, cmd=str(body.get("cmd", "")), params=params)

    def command(self) -> tuple[str, str, dict[str, Any]]:
        """Return ``(object, verb, inline_params)``.

        ``vnd.logitech.setup/vnd.logitech.account?getProvisionInfo`` -> ``("account",
        "getProvisionInfo", {})``. Inline ``&key=value`` pairs after the verb (as used by
        ``?provision&provisionInfo=<urlencoded json>``) are decoded into the dict.
        """
        cmd = self.cmd.strip()
        target, _, rest = cmd.partition("?")
        obj = target.rsplit("/", 1)[-1].removeprefix("vnd.logitech.").rsplit(".", 1)[-1]
        verb, *pairs = rest.split("&")
        inline: dict[str, Any] = {}
        for pair in pairs:
            k, _, v = pair.partition("=")
            value: Any = unquote_plus(v)
            if isinstance(value, str) and value[:1] in "{[":
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            inline[k] = value
        return obj, verb, inline


class ProvisionInfo(BaseModel):
    """What the MyHarmony client writes to a hub with ``account?provision``.

    Field list from the decompiled MyHarmony ``ProvisionController.SaveProvision``.
    """

    model_config = ConfigDict(extra="allow")

    #: Seen in the /sys/hlapi (USB) variant of the blob.
    domain: str | None = None
    globalRemoteId: str | None = None
    authToken: str | None = None
    accountId: str | None = None
    email: str | None = None
    username: str | None = None
    discoveryServer: str | None = None
    mode: str | None = None
    activeRemoteId: str | None = None
    name: str | None = None
    language: str | None = None
    susChannel: str | None = None


class ProvisionData(BaseModel):
    accountId: str | None
    activeRemoteId: str
    globalRemoteId: str
    discoveryServer: str
    discoveryServerCF: str
    hubProfiles: dict[str, str] = Field(default_factory=lambda: {"Harmony": "2.0"})
    productId: str = "Pimento"
    mode: int = 3
    accountOverrideUrl: str | None = None
    # Extras seen in MyHarmony's parser of the hub-local response:
    email: str | None = None
    username: str | None = None
    language: str = "en-US"
    cloudAuth: str
    token: str


class ClaimRequest(BaseModel):
    hubId: str = Field(description="globalRemoteId (numeric) or the hub's uuid")
    hubName: str | None = None
    ipAddress: str | None = None


class HubOut(BaseModel):
    id: int
    globalRemoteId: str
    hubUuid: str | None
    friendlyName: str
    accountId: str | None
    ipAddress: str | None
    hubSwVersion: str | None
    lastSeen: str | None
    syncedConfigVersion: int | None
    cloudAuth: str | None = None
