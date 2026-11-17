from __future__ import annotations

from fastapi.testclient import TestClient


def test_search_manufacturers(client: TestClient) -> None:
    r = client.get("/api/devices/", params={"q": "son"})
    names = [m["name"] for m in r.json()]
    assert names[:2] == ["Sony", "Sonos"] or set(names) == {"Sony", "Sonos"}
    assert client.get("/api/devices/", params={"q": "sony"}).json()[0]["slug"] == "Sony"


def test_models_list(client: TestClient) -> None:
    r = client.get("/api/devices/Sony/")
    body = r.json()
    assert body["manufacturer"] == "Sony" and body["count"] == 6
    assert any(m["model"] == "1040Q" for m in body["models"])
    assert client.get("/api/devices/sony/", params={"q": "1040"}).json()["count"] == 1
    assert client.get("/api/devices/NoSuchBrand/").status_code == 404


def test_device_with_inlined_codeset(client: TestClient) -> None:
    d = client.get("/api/devices/Sony/1040Q").json()
    assert d["globalDeviceId"] == 6655 and d["power"]["type"] == "discrete"
    assert len(d["commands"]) == 48 and d["commands"][0]["pronto"].startswith("0000")
    lean = client.get("/api/devices/Sony/1040Q", params={"inline": False}).json()
    assert "commands" not in lean
    # by globalDeviceId as well
    assert client.get("/api/devices/Sony/6655").json()["model"] == "1040Q"


def test_device_path_traversal_is_impossible(client: TestClient) -> None:
    for bad in ("../../manifest", "..%2F..%2Fmanifest.json", "1040Q/../../index"):
        assert client.get(f"/api/devices/Sony/{bad}").status_code == 404


def test_protocols(client: TestClient) -> None:
    p = client.get("/api/protocols/NEC_32_Bit").json()
    assert p["carrierHz"] == 37888
    assert client.get("/api/protocols/NEC 32 Bit").status_code == 200
    assert client.get("/api/protocols/nope").status_code == 404
    assert len(client.get("/api/protocols/").json()) == 2


def test_static_files(client: TestClient) -> None:
    r = client.get("/device-db/manifest.json")
    assert r.status_code == 200 and r.json()["schemaVersion"] == 2
    # also on the content vhost
    assert (
        client.get(
            "/device-db/manifest.json", headers={"Host": "content.dhg.myharmony.com"}
        ).status_code
        == 200
    )


def test_missing_device_db_returns_503(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from harmony_server.config import Settings
    from harmony_server.main import create_app

    s = Settings(data_dir=tmp_path, device_db_path=tmp_path / "missing", enable_tls=False)
    with TestClient(create_app(s)) as c:
        assert c.get("/api/devices/").status_code == 503
