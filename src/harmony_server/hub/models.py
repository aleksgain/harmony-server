"""Portable config model used by the new client's REST API.

Shapes intentionally mirror what the hub returns from its local
``vnd.logitech.harmony.engine?config`` command (``device[].controlGroup[].function[]``,
``activity[]``) so a config read from a live hub can be PUT back unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommandFunction(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    label: str | None = None
    #: Hub form is a JSON *string*: '{"command":"PowerOn","type":"IRCommand","deviceId":"123"}'.
    action: str | dict[str, Any] | None = None


class ControlGroup(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    function: list[CommandFunction] = Field(default_factory=list)


class DeviceIn(BaseModel):
    #: Hub-side device id. Allocated by the server when omitted.
    id: str | None = Field(default=None, alias="deviceId")
    label: str
    type: str = "Television"
    manufacturer: str = ""
    model: str = ""
    #: Device-DB catalogue id (``globalDeviceId`` in the IR archive).
    globalDeviceId: int | None = None
    #: Full command groups; if omitted they are generated from ``commands`` or the device DB.
    controlGroup: list[ControlGroup] | None = None
    #: Shortcut: flat list of command names.
    commands: list[str] | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ActivityIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="activityId")
    label: str
    type: str = "VirtualGeneric"
    #: Device ids taking part in the activity.
    devices: list[str] = Field(default_factory=list)
    #: Per-device desired state, e.g. ``{"123": {"Power": "On", "Input": "HDMI1"}}``.
    fixit: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: Role -> deviceId, e.g. ``{"VolumeActivityRole": "123"}``.
    roles: dict[str, str] = Field(default_factory=dict)
    sequences: list[Any] = Field(default_factory=list)
    controlGroup: list[ControlGroup] | None = None
    activityOrder: int | None = None


class HubConfigIn(BaseModel):
    devices: list[DeviceIn] = Field(default_factory=list, alias="device")
    activities: list[ActivityIn] = Field(default_factory=list, alias="activity")

    model_config = ConfigDict(populate_by_name=True)


class HubConfigOut(BaseModel):
    hubId: str
    configVersion: int
    contentVersion: int
    activity: list[dict[str, Any]]
    device: list[dict[str, Any]]
    sequence: list[Any] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    global_: dict[str, Any] = Field(default_factory=dict, alias="global")

    model_config = ConfigDict(populate_by_name=True)


class ActivityAction(BaseModel):
    activityId: str = Field(description="Activity id; '-1' powers everything off")
    action: Literal["start", "stop"] = "start"


class CommandRequest(BaseModel):
    deviceId: str
    command: str
    delay: float = Field(default=0.0, ge=0, le=10)
    repeat: int = Field(default=1, ge=1, le=20)


class HubStatus(BaseModel):
    hubId: str
    online: bool
    ipAddress: str | None
    currentActivityId: str | None = None
    currentActivityLabel: str | None = None
    hubConfigVersion: int | None = None
    serverConfigVersion: int | None = None
    syncRequired: bool | None = None
    hubSwVersion: str | None = None
    error: str | None = None
