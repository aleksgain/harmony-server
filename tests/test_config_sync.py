from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import SVCS, auth

SOAP = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<{op} xmlns="http://tempuri.org/"><clientTypeId>HarmonyTouch</clientTypeId><clientId/></{op}>
</s:Body></s:Envelope>"""


def _claimed_hub(client: TestClient, user: dict[str, str]) -> str:
    r = client.post(
        "/api/hubs/claim", headers=auth(user), json={"hubId": "HUB-UUID-1", "hubName": "Den"}
    )
    assert r.status_code == 201, r.text
    return r.json()["globalRemoteId"]


CONFIG = {
    "devices": [
        {"label": "TV", "type": "Television", "manufacturer": "Sony", "model": "1040Q"},
        {
            "deviceId": "77",
            "label": "AVR",
            "type": "AudioVideoReceiver",
            "commands": ["PowerOn", "PowerOff", "VolumeUp", "VolumeDown", "Mute", "InputHdmi1"],
        },
    ],
    "activities": [
        {
            "label": "Watch TV",
            "devices": ["77"],
            "roles": {"VolumeActivityRole": "77"},
            "fixit": {"77": {"Input": "HDMI 1"}},
        },
    ],
}


def test_soap_get_json2_uris_returns_service_directory(client: TestClient) -> None:
    r = client.post(
        "/Discovery/Discovery.svc",
        content=SOAP.format(op="GetJson2Uris"),
        headers={
            **SVCS,
            "Content-Type": "text/xml",
            "SOAPAction": '"http://tempuri.org/IDiscovery/GetJson2Uris"',
        },
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/xml")
    assert "<GetJson2UrisResult" in r.text
    assert "https://svcs.myharmony.com/SyncPlatform/CompileManager.svc/json2/StartCompile" in r.text
    assert "Logitech-API-Key" not in r.text


def test_soap_get_all_services_and_rewrites(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    settings.service_url_rewrites = {"https://svcs.myharmony.com": "https://harmony.lan"}
    r = client.post(
        "/Discovery/Discovery.svc",
        content=SOAP.format(op="GetAllServices"),
        headers={**SVCS, "Content-Type": "text/xml"},
    )
    for grp in ("a:Json2Services", "a:JsonServices", "a:SoapServices"):
        assert f"<{grp}>" in r.text
    assert "https://harmony.lan/Discovery/Discovery.svc" in r.text
    assert "https://svcs.myharmony.com/" not in r.text


def test_soap_unknown_op_faults(client: TestClient) -> None:
    r = client.post(
        "/Discovery/Discovery.svc",
        content=SOAP.format(op="Nope"),
        headers={**SVCS, "Content-Type": "text/xml"},
    )
    assert r.status_code == 500 and "Fault" in r.text


def test_config_put_compiles_and_bumps_version(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    r = client.put(f"/api/hubs/{rid}/config", headers=auth(user), json=CONFIG)
    assert r.status_code == 200, r.text
    assert r.json()["configVersion"] == 1
    cfg = client.get(f"/api/hubs/{rid}/config", headers=auth(user)).json()
    assert cfg["configVersion"] == 1 and cfg["contentVersion"] == 1
    tv = next(d for d in cfg["device"] if d["label"] == "TV")
    # commands pulled from the device DB and grouped like engine?config
    groups = {g["name"] for g in tv["controlGroup"]}
    assert {"Power", "NumericBasic"} <= groups
    fn = tv["controlGroup"][0]["function"][0]
    assert json.loads(fn["action"])["deviceId"] == tv["id"]
    assert tv["globalDeviceId"] == 6655
    watch = next(a for a in cfg["activity"] if a["label"] == "Watch TV")
    assert watch["fixit"]["77"] == {"id": "77", "Power": "On", "Input": "HDMI 1"}
    assert any(g["name"] == "Volume" for g in watch["controlGroup"])
    assert any(a["id"] == "-1" for a in cfg["activity"])

    # same devices, activity renamed -> configVersion bumps, contentVersion does not
    changed = json.loads(json.dumps(CONFIG))
    changed["devices"][0]["deviceId"] = tv["id"]
    changed["activities"][0]["label"] = "TV time"
    r2 = client.put(f"/api/hubs/{rid}/config", headers=auth(user), json=changed)
    assert r2.json() == {"configVersion": 2, "contentVersion": 1, "hubNotified": False}


def test_config_rejects_unknown_device_reference(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    bad = {"devices": [], "activities": [{"label": "X", "devices": ["999"]}]}
    assert client.put(f"/api/hubs/{rid}/config", headers=auth(user), json=bad).status_code == 422


def test_json_config_sync_current_vs_update(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    url = "/Discovery/Discovery.svc"
    r = client.post(url, headers=SVCS, json={"hubId": rid, "configVersion": 0})
    assert r.json()["msg"] == "No config stored"
    client.put(f"/api/hubs/{rid}/config", headers=auth(user), json=CONFIG)

    stale = client.post(url, headers=SVCS, json={"hubId": rid, "configVersion": 0}).json()
    assert stale["data"]["syncRequired"] is True
    bundle = stale["data"]["config"]
    for key in ("activity", "device", "configVersion", "contentVersion"):
        assert key in bundle
    current = client.post(
        url, headers=SVCS, json={"hubId": rid, "configVersion": 1, "contentVersion": 1}
    ).json()
    assert current["data"]["syncRequired"] is False and "config" not in current["data"]

    cm = "/SyncPlatform/CompileManager.svc/json2"
    assert (
        client.post(f"{cm}/IsRemoteSyncRequired", headers=SVCS, json={"remoteId": rid}).json()
        is True
    )
    sc = client.post(f"{cm}/StartCompile", headers=SVCS, json={"remoteId": rid}).json()
    assert set(sc) >= {"DownloadUrl", "SimulationDownloadUrl", "ApproximateSize"}
    path = sc["DownloadUrl"].removeprefix("https://svcs.myharmony.com")
    dl = client.get(path, headers=SVCS)
    assert dl.status_code == 200 and dl.json()["configVersion"] == 1

    rm = "/RemotePlatform/RemoteManager.svc/json2"
    client.post(
        f"{rm}/RegisterSuccessfulSync", headers=SVCS, json={"remoteId": rid, "configVersion": 1}
    )
    assert (
        client.post(f"{cm}/IsRemoteSyncRequired", headers=SVCS, json={"remoteId": rid}).json()
        is False
    )


def test_provision_blob(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    b = client.get(
        f"/api/hubs/{rid}/provision-blob?discovery_host=harmony.lan", headers=auth(user)
    ).json()
    info = b["hlapi"]["params"]["provisionInfo"]
    assert b["hlapi"]["command"] == "account?provision"
    assert info["discoveryServer"] == "https://harmony.lan/Discovery/Discovery.svc"
    assert info["domain"] == "harmony.lan" and info["globalRemoteId"] == rid


def test_other_accounts_cannot_see_hub(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    other = client.post(
        "/api/auth/register", json={"email": "bob@example.com", "password": "password123"}
    ).json()
    assert client.get(f"/api/hubs/{rid}/config", headers=auth(other)).status_code == 404
    assert (
        client.post("/api/hubs/claim", headers=auth(other), json={"hubId": rid}).status_code == 409
    )


def test_legacy_app_data_endpoints(client: TestClient, user: dict[str, str]) -> None:
    rid = _claimed_hub(client, user)
    client.put(f"/api/hubs/{rid}/config", headers=auth(user), json=CONFIG)
    h = {**SVCS, "Authorization": user["token"]}
    acct = client.post("/HarmonyPlatform/AccountManager.svc/json2/GetMyAccount", headers=h).json()
    assert acct["Email"] == "alice@example.com" and acct["Remotes"][0]["Id"] == int(rid)
    devs = client.post(
        "/UserAccountDirectorPlatform/UserAccountDirector.svc/json2/GetDeviceList", headers=h
    ).json()
    assert {d["Name"] for d in devs} == {"TV", "AVR"}
    acts = client.get("/ActivityPlatform/ActivityManager.svc/json2/GetActivities", headers=h).json()
    assert [a["Name"] for a in acts] == ["Watch TV"]
    assert (
        client.post(
            "/HarmonyPlatform/AccountManager.svc/json2/GetMyAccount", headers=SVCS
        ).status_code
        == 401
    )
