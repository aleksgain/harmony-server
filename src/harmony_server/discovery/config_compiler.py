"""Compile stored devices/activities into the bundle a hub syncs.

TODO: validate against captured Discovery.svc packet.
    The bundle layout below is the *inverse of the hub's local*
    ``vnd.logitech.harmony.engine?config`` response (``activity[]``, ``device[]`` with
    ``controlGroup[].function[]`` whose ``action`` is a JSON string), plus the
    ``configVersion``/``contentVersion`` counters the hub reports in its state digest.
    What the hub actually downloads from the cloud may be a different, compiled format
    (Logitech's ``CompileManager.StartCompile`` suggests a server-side build step, and the
    hub also runs Lua engines). Until a capture exists this is a best-effort guess, and a
    real hub may ignore or reject it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from harmony_server.db import Activity, Device, HarmonyConfig, Hub
from harmony_server.device_db.store import DeviceDB
from harmony_server.hub.models import ActivityIn, ControlGroup, DeviceIn, HubConfigIn

log = logging.getLogger(__name__)

BUNDLE_FORMAT = "harmony-server/engine-config-inverse/v0"

# Group names as they appear in real engine?config dumps.
_GROUP_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Power", re.compile(r"^Power", re.I)),
    ("NumericBasic", re.compile(r"^(\d|Number\d|Dash|Hyphen|Enter)$", re.I)),
    ("Volume", re.compile(r"^(Volume|Mute)", re.I)),
    ("Channel", re.compile(r"^(Channel|Prev(ious)?Channel|Last)", re.I)),
    ("NavigationBasic", re.compile(r"^(Direction|Select|OK|Menu|Exit|Back|Return|Home)", re.I)),
    ("NavigationExtended", re.compile(r"^(Guide|Info|PageUp|PageDown|Options|Setup)", re.I)),
    ("TransportBasic", re.compile(r"^(Play|Pause|Stop|Rewind|FastForward|Record)$", re.I)),
    ("TransportExtended", re.compile(r"^(Skip|Eject|FrameAdvance|Slow)", re.I)),
    ("ColoredButtons", re.compile(r"^(Red|Green|Yellow|Blue)$", re.I)),
    ("Input", re.compile(r"^Input", re.I)),
]
_GROUP_ORDER = [g for g, _ in _GROUP_RULES] + ["Other"]


def group_for(command: str) -> str:
    for group, rx in _GROUP_RULES:
        if rx.search(command):
            return group
    return "Other"


def _label(command: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", command)


def make_action(device_id: str, command: str) -> str:
    # Exactly the string form the hub uses (and aioharmony/holdAction expects).
    return json.dumps(
        {"command": command, "type": "IRCommand", "deviceId": device_id}, separators=(",", ":")
    )


def control_groups_from_commands(device_id: str, commands: list[str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for name in commands:
        groups.setdefault(group_for(name), []).append(
            {"name": name, "label": _label(name), "action": make_action(device_id, name)}
        )
    return [{"name": g, "function": groups[g]} for g in _GROUP_ORDER if g in groups]


def _normalise_groups(device_id: str, groups: list[ControlGroup]) -> list[dict[str, Any]]:
    out = []
    for g in groups:
        funcs = []
        for f in g.function:
            action = f.action
            if action is None:
                action = make_action(device_id, f.name)
            elif isinstance(action, dict):
                action = json.dumps({**action, "deviceId": device_id}, separators=(",", ":"))
            funcs.append({"name": f.name, "label": f.label or _label(f.name), "action": action})
        out.append({"name": g.name, "function": funcs})
    return out


async def _allocate_id(existing: set[str], base: int) -> str:
    n = base
    while str(n) in existing:
        n += 1
    existing.add(str(n))
    return str(n)


async def latest_config(session: AsyncSession, hub_id: int) -> HarmonyConfig | None:
    stmt = (
        select(HarmonyConfig)
        .where(HarmonyConfig.hub_id == hub_id)
        .order_by(col(HarmonyConfig.config_version).desc())
        .limit(1)
    )
    return (await session.exec(stmt)).first()


async def resolve_device_commands(dev: DeviceIn, device_db: DeviceDB | None) -> list[str]:
    if dev.commands:
        return list(dev.commands)
    if device_db is None or not device_db.available or not dev.manufacturer or not dev.model:
        return []
    record = await device_db.device(dev.manufacturer, dev.model, inline_codeset=True)
    if record is None:
        log.info("Device %s/%s not in device DB", dev.manufacturer, dev.model)
        return []
    if dev.globalDeviceId is None:
        dev.globalDeviceId = record.get("globalDeviceId")
    return [c["name"] for c in record.get("commands", []) if "name" in c]


def _derive_activity_groups(
    act: ActivityIn, device_groups: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Build an activity's button map from its device roles (MyHarmony's default logic)."""
    if not act.devices:
        return []
    first = act.devices[0]
    role_src = {
        "Volume": act.roles.get("VolumeActivityRole", first),
        "Channel": act.roles.get("ChannelChangingActivityRole", first),
        "NumericBasic": act.roles.get("ChannelChangingActivityRole", first),
    }
    control = act.roles.get("ControlActivityRole", act.roles.get("DisplayActivityRole", first))
    out: list[dict[str, Any]] = []
    for gname in _GROUP_ORDER:
        if gname in ("Power", "Input", "Other"):
            continue
        src = role_src.get(gname, control)
        for g in device_groups.get(src, []):
            if g["name"] == gname:
                out.append(g)
    return out


def _content_hash(devices: list[dict[str, Any]]) -> str:
    blob = json.dumps(devices, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


async def save_config(
    session: AsyncSession, hub: Hub, cfg: HubConfigIn, device_db: DeviceDB | None
) -> HarmonyConfig:
    """Replace a hub's devices/activities and publish a new config version."""
    assert hub.id is not None
    used_dev: set[str] = {d.id for d in cfg.devices if d.id}
    used_act: set[str] = {a.id for a in cfg.activities if a.id}

    device_rows: list[Device] = []
    device_groups: dict[str, list[dict[str, Any]]] = {}
    for dev in cfg.devices:
        dev_id = dev.id or await _allocate_id(used_dev, 50_000_001)
        if dev.controlGroup is not None:
            groups = _normalise_groups(dev_id, dev.controlGroup)
        else:
            groups = control_groups_from_commands(
                dev_id, await resolve_device_commands(dev, device_db)
            )
        device_groups[dev_id] = groups
        extra = dev.model_dump(
            exclude={
                "id",
                "label",
                "type",
                "manufacturer",
                "model",
                "globalDeviceId",
                "controlGroup",
                "commands",
            },
            by_alias=False,
        )
        device_rows.append(
            Device(
                hub_id=hub.id,
                device_id=dev_id,
                label=dev.label,
                type=dev.type,
                manufacturer=dev.manufacturer,
                model=dev.model,
                global_device_id=dev.globalDeviceId,
                control_groups_json=json.dumps(groups),
                extra_json=json.dumps(extra),
            )
        )

    known = set(device_groups)
    activity_rows: list[Activity] = []
    for order, act in enumerate(cfg.activities):
        unknown = [d for d in act.devices if d not in known]
        if unknown:
            raise ValueError(f"Activity {act.label!r} references unknown devices {unknown}")
        act_id = act.id or await _allocate_id(used_act, 40_000_001)
        groups = (
            [g.model_dump() for g in act.controlGroup]
            if act.controlGroup is not None
            else _derive_activity_groups(act, device_groups)
        )
        fixit = {d: {"id": d, "Power": "On", **act.fixit.get(d, {})} for d in act.devices}
        extra = {
            "fixit": fixit,
            "roles": act.roles,
            "controlGroup": groups,
            "activityOrder": act.activityOrder if act.activityOrder is not None else order,
            **act.model_dump(
                exclude={
                    "id",
                    "label",
                    "type",
                    "devices",
                    "fixit",
                    "roles",
                    "sequences",
                    "controlGroup",
                    "activityOrder",
                }
            ),
        }
        activity_rows.append(
            Activity(
                hub_id=hub.id,
                activity_id=act_id,
                label=act.label,
                type=act.type,
                devices_json=json.dumps(act.devices),
                sequence_json=json.dumps(act.sequences),
                extra_json=json.dumps(extra),
            )
        )

    await session.exec(delete(Device).where(col(Device.hub_id) == hub.id))
    await session.exec(delete(Activity).where(col(Activity.hub_id) == hub.id))
    session.add_all(device_rows)
    session.add_all(activity_rows)
    await session.flush()

    bundle = await compile_bundle(session, hub)
    prev = await latest_config(session, hub.id)
    content_hash = bundle["_meta"]["contentHash"]
    if prev is None:
        config_version, content_version = 1, 1
    else:
        prev_hash = json.loads(prev.payload_json).get("_meta", {}).get("contentHash")
        config_version = prev.config_version + 1
        content_version = prev.content_version + (0 if prev_hash == content_hash else 1)
    bundle["configVersion"] = config_version
    bundle["contentVersion"] = content_version
    row = HarmonyConfig(
        hub_id=hub.id,
        config_version=config_version,
        content_version=content_version,
        payload_json=json.dumps(bundle),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    log.info("Hub %s: published configVersion=%d", hub.global_remote_id, config_version)
    return row


async def compile_bundle(session: AsyncSession, hub: Hub) -> dict[str, Any]:
    """Assemble the engine?config-shaped bundle from DB rows (versions filled by caller)."""
    devices = (
        await session.exec(select(Device).where(Device.hub_id == hub.id).order_by(col(Device.id)))
    ).all()
    activities = (
        await session.exec(
            select(Activity).where(Activity.hub_id == hub.id).order_by(col(Activity.id))
        )
    ).all()

    device_out: list[dict[str, Any]] = []
    for d in devices:
        extra = json.loads(d.extra_json)
        device_out.append(
            {
                **extra,
                "id": d.device_id,
                "label": d.label,
                "type": d.type,
                "manufacturer": d.manufacturer,
                "model": d.model,
                "deviceTypeDisplayName": d.type,
                "globalDeviceId": d.global_device_id,
                "isManualPower": "false",
                "controlGroup": json.loads(d.control_groups_json),
            }
        )

    activity_out: list[dict[str, Any]] = []
    for a in activities:
        extra = json.loads(a.extra_json)
        activity_out.append(
            {
                **extra,
                "id": a.activity_id,
                "label": a.label,
                "type": a.type,
                "isAVActivity": a.type not in ("PowerOff",),
                "sequences": json.loads(a.sequence_json),
            }
        )
    # Every hub config has the implicit PowerOff activity with id -1.
    activity_out.append(
        {
            "id": "-1",
            "label": "PowerOff",
            "type": "PowerOff",
            "isAVActivity": False,
            "activityOrder": 0,
            "controlGroup": [],
            "fixit": {},
            "sequences": [],
        }
    )

    return {
        "hubId": str(hub.global_remote_id),
        "configVersion": None,
        "contentVersion": None,
        "activity": activity_out,
        "device": device_out,
        "sequence": [],
        "content": {
            "contentUserHost": "https://content.dhg.myharmony.com/content/user/{userProfileUri}",
            "contentDeviceHost": "https://content.dhg.myharmony.com/content/device/{deviceProfileUri}",
            "contentServiceHost": "https://content.dhg.myharmony.com/{serviceProfileUri}",
            "householdUserProfileUri": "",
        },
        "global": {"locale": "en-US"},
        "_meta": {"format": BUNDLE_FORMAT, "contentHash": _content_hash(device_out)},
    }


def sync_decision(
    latest: HarmonyConfig | None, hub_config_version: int | None, hub_content_version: int | None
) -> str:
    """``none`` (nothing stored), ``current`` (hub up to date) or ``update``."""
    if latest is None:
        return "none"
    if hub_config_version is None:
        return "update"
    if hub_config_version < latest.config_version:
        return "update"
    if hub_content_version is not None and hub_content_version < latest.content_version:
        return "update"
    return "current"
