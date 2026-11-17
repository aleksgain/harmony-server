"""Endpoints and identifiers the harmony-client desktop app relies on."""

from fastapi.testclient import TestClient

from .conftest import auth


def test_api_health_alias_matches_healthz(client: TestClient) -> None:
    a = client.get("/api/health")
    b = client.get("/healthz")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_api_health_ignores_vhost(client: TestClient) -> None:
    r = client.get("/api/health", headers={"Host": "svcs.myharmony.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_hub_routes_accept_uuid(client: TestClient, user: dict[str, str]) -> None:
    uuid = "6bd1b7c5-1e47-4f4f-9a0a-1a2b3c4d5e6f"
    r = client.post(
        "/api/hubs/claim",
        json={"hubId": uuid, "hubName": "Living room", "ipAddress": "192.0.2.10"},
        headers=auth(user),
    )
    assert r.status_code == 201, r.text
    assert client.get(f"/api/hubs/{uuid}/status", headers=auth(user)).status_code != 404


def test_hub_routes_accept_remote_id(client: TestClient, user: dict[str, str]) -> None:
    r = client.post(
        "/api/hubs/claim",
        json={"hubId": "19159295", "hubName": "Living room", "ipAddress": "192.0.2.10"},
        headers=auth(user),
    )
    assert r.status_code == 201, r.text
    assert r.json()["globalRemoteId"] == "19159295"
    assert client.get("/api/hubs/19159295/config", headers=auth(user)).status_code in (200, 404)


def test_other_accounts_cannot_resolve_by_uuid(client: TestClient, user: dict[str, str]) -> None:
    uuid = "0f0e0d0c-0b0a-4908-8706-050403020100"
    client.post("/api/hubs/claim", json={"hubId": uuid}, headers=auth(user))
    other = client.post(
        "/api/auth/register", json={"email": "bob@example.com", "password": "hunter22hunter"}
    ).json()
    assert client.get(f"/api/hubs/{uuid}/status", headers=auth(other)).status_code == 404
