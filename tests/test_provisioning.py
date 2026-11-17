from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import SVCS, auth

GPI = {"id": 1, "cmd": "setup.account?getProvisionInfo", "params": {}}


def test_get_provision_info_auto_registers_unknown_hub(client: TestClient) -> None:
    r = client.post("/", json=GPI, headers=SVCS)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200 and body["msg"] == "OK" and body["id"] == 1
    assert body["cmd"] == "setup.account?getProvisionInfo"
    d = body["data"]
    assert d["accountId"] is None  # unclaimed
    assert d["activeRemoteId"] == d["globalRemoteId"] and d["activeRemoteId"].isdigit()
    assert d["discoveryServer"] == "https://svcs.myharmony.com"
    assert d["discoveryServerCF"] == "https://content.dhg.myharmony.com"
    assert d["hubProfiles"] == {"Harmony": "2.0"}
    assert d["productId"] == "Pimento" and d["mode"] == 3
    assert d["accountOverrideUrl"] is None
    # MyHarmony-style mirror fields
    assert body["errorCode"] == "200" and body["result"] == d


def test_same_hub_is_recognised_again(client: TestClient) -> None:
    first = client.post("/", json=GPI, headers=SVCS).json()["data"]
    again = client.post(
        "/", json={**GPI, "params": {"hubId": first["globalRemoteId"]}}, headers=SVCS
    )
    assert again.json()["data"]["globalRemoteId"] == first["globalRemoteId"]
    # identified by cloudAuth blob too
    blob = first["cloudAuth"]
    assert blob.startswith("svcs.myharmony.com;;" + first["globalRemoteId"] + ";")
    by_blob = client.post("/", json=GPI, headers={**SVCS, "Authorization": blob})
    assert by_blob.json()["data"]["globalRemoteId"] == first["globalRemoteId"]
    assert by_blob.json()["data"]["token"] == first["token"]
    # knowing only the numeric id must not leak the hub's secret token
    assert again.json()["data"]["token"] is None and again.json()["data"]["cloudAuth"] is None


def test_long_mime_and_ws_envelope_accepted(client: TestClient) -> None:
    r = client.post(
        "/",
        headers=SVCS,
        json={
            "hubId": "x",
            "timeout": 5,
            "hbus": {
                "id": "abc",
                "cmd": "vnd.logitech.setup/vnd.logitech.account?getProvisionInfo",
                "params": {},
            },
        },
    )
    assert r.json()["code"] == 200 and r.json()["id"] == "abc"


def test_provision_claims_hub_and_provision_info_reflects_account(
    client: TestClient, user: dict[str, str]
) -> None:
    rid = client.post("/", json=GPI, headers=SVCS).json()["data"]["globalRemoteId"]
    info = {
        "authToken": user["hubToken"],
        "accountId": user["accountId"],
        "activeRemoteId": rid,
        "name": "Living Room",
        "mode": "3",
        "discoveryServer": "https://svcs.myharmony.com/Discovery/Discovery.svc",
    }
    # Exactly how MyHarmony encodes it: inline, url-encoded, in the cmd string.
    import json
    from urllib.parse import quote_plus

    cmd = "vnd.logitech.setup/vnd.logitech.account?provision&provisionInfo=" + quote_plus(
        json.dumps(info)
    )
    r = client.post("/", json={"id": 2, "cmd": cmd, "params": {}}, headers=SVCS)
    assert r.json()["code"] == 200, r.json()
    assert r.json()["data"]["cloudAuth"].split(";")[1:3] == [user["accountId"], rid]
    d = client.post("/", json={**GPI, "params": {"hubId": rid}}, headers=SVCS).json()["data"]
    assert d["accountId"] == user["accountId"]
    assert d["email"] == "alice@example.com"
    hubs = client.get("/api/hubs/", headers=auth(user)).json()
    assert [h["friendlyName"] for h in hubs] == ["Living Room"]


def test_provision_hlapi_shape(client: TestClient, user: dict[str, str]) -> None:
    rid = client.post("/", json=GPI, headers=SVCS).json()["data"]["globalRemoteId"]
    r = client.post(
        "/",
        headers=SVCS,
        json={
            "command": "account?provision",
            "params": {
                "provisionInfo": {
                    "domain": "svcs.myharmony.com",
                    "accountId": user["accountId"],
                    "globalRemoteId": rid,
                    "authToken": user["hubToken"],
                    "discoveryServer": "svcs.myharmony.com",
                    "mode": "1",
                }
            },
        },
    )
    assert r.json()["code"] == 200
    assert r.json()["data"]["accountId"] == user["accountId"]


def test_provision_rejects_bad_token(client: TestClient) -> None:
    r = client.post(
        "/",
        headers=SVCS,
        json={"cmd": "setup.account?provision", "params": {"provisionInfo": {"authToken": "nope"}}},
    )
    assert r.json()["code"] == 401


def test_get_global_remote(client: TestClient) -> None:
    rid = client.post("/", json=GPI, headers=SVCS).json()["data"]["globalRemoteId"]
    r = client.post(
        "/", headers=SVCS, json={"cmd": "setup.account?getGlobalRemote", "params": {"hubId": rid}}
    )
    assert r.json()["data"]["globalRemoteId"] == rid


def test_host_routing_isolates_vhosts(client: TestClient) -> None:
    # POST / on a non-Logitech host is not the provisioning endpoint.
    assert client.post("/", json=GPI).status_code == 405
    # setup.myharmony.com also answers hbus commands
    assert client.post("/", json=GPI, headers={"Host": "setup.myharmony.com"}).json()["code"] == 200
    # internal prefix is not addressable directly
    assert client.post("/_vh/svcs/", json=GPI).status_code == 404
    # shared API works on Logitech hostnames too
    assert client.get("/healthz", headers=SVCS).json()["status"] == "ok"


def test_unknown_command_and_capture(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/", headers=SVCS, json={"cmd": "setup.account?frobnicate"})
    assert r.json()["code"] == 501
    r = client.post("/Some/Unknown.svc/json2/Op", headers=SVCS, content=b'{"x":1}')
    assert r.status_code == 501 and r.json()["captured"] is True
    lines = (settings.resolved_capture_dir / "svcs.jsonl").read_text().splitlines()
    assert any('"Some/Unknown.svc/json2/Op"' in ln or "Unknown.svc" in ln for ln in lines)


def test_wcf_provision_hub_and_cloud_token(client: TestClient, user: dict[str, str]) -> None:
    uad = "/UserAccountDirectorPlatform/UserAccountDirector.svc/json2"
    r = client.post(
        f"{uad}/ProvisionHub",
        headers={**SVCS, "Authorization": user["hubToken"]},
        json={"unitCertificate": "CERT-123", "remoteInfo": {}},
    )
    body = r.json()
    assert body["HubCloudToken"] and isinstance(body["HubId"], int)
    t = client.post(f"{uad}/GetHubCloudToken", headers=SVCS, json={"unitCertificate": "CERT-123"})
    assert t.json()["HubCloudToken"] == body["HubCloudToken"]
    assert len(client.get("/api/hubs/", headers=auth(user)).json()) == 1


def test_legacy_three_step_auth_chain(client: TestClient, user: dict[str, str]) -> None:
    creds = {"email": "alice@example.com", "password": "hunter22hunter"}
    s1 = client.post("/identity/signin", json=creds, headers={"Host": "accounts.logi.com"})
    assert s1.status_code == 200
    access = s1.json()["access_token"]
    s2 = client.post(
        "/CompositeSecurityServices/Security.svc/json2/signin",
        headers=SVCS,
        json={"UserAuthToken": access},
    )
    assert s2.status_code == 200 and "harmonyauth" in s2.cookies
    s3 = client.post(
        "/CompositeSecurityServices/Security.svc/json2/GetAuthenticatedUserAuthToken",
        headers={**SVCS, "Authorization": s2.json()["AuthToken"]},
        json={},
    )
    hub_token = s3.json()["UserAuthToken"]
    # the step-3 token provisions a hub
    rid = client.post("/", json=GPI, headers=SVCS).json()["data"]["globalRemoteId"]
    r = client.post(
        "/",
        headers=SVCS,
        json={
            "cmd": "setup.account?provision",
            "params": {"provisionInfo": {"authToken": hub_token, "activeRemoteId": rid}},
        },
    )
    assert r.json()["code"] == 200
    # but a step-1 token must not be accepted as a hub token or API token
    assert (
        client.get("/api/hubs/", headers={"Authorization": f"Bearer {access}"}).status_code == 401
    )


def test_firmware_says_no_update(client: TestClient) -> None:
    r = client.get(
        "/SoftwareUpdatesPlatform/SoftwareUpdates/product/Pimento/unit/123/info",
        headers={"Host": "sus.dhg.myharmony.com"},
    )
    assert r.json()["updateAvailable"] is False
    assert (
        client.get("/api/firmware/check?hubSwVersion=4.15.600").json()["updateAvailable"] is False
    )
