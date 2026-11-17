from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from harmony_server.config import Settings
from harmony_server.main import create_app
from harmony_server.middleware import proxy


def test_proxy_relays_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"from": "logitech"}, headers={"x-up": "1"})

    real = httpx.AsyncClient

    def fake_client(**kw: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(upstream))

    monkeypatch.setattr(proxy.httpx, "AsyncClient", fake_client)
    s = Settings(
        data_dir=tmp_path,
        device_db_path=tmp_path / "x",
        enable_tls=False,
        server_mode="proxy",
        proxy_upstream_ips={"svcs.myharmony.com": "203.0.113.5"},
    )
    with TestClient(create_app(s)) as c:
        r = c.post(
            "/Discovery/Discovery.svc?x=1",
            content=b"<soap/>",
            headers={"Host": "svcs.myharmony.com", "Authorization": "secret"},
        )
        assert r.status_code == 200 and r.json() == {"from": "logitech"}
        # shared API is still served locally
        assert c.get("/healthz", headers={"Host": "svcs.myharmony.com"}).json()["mode"] == "proxy"
    assert str(seen[0].url) == "https://203.0.113.5/Discovery/Discovery.svc?x=1"
    assert seen[0].headers["host"] == "svcs.myharmony.com"
    assert seen[0].extensions.get("sni_hostname") == "svcs.myharmony.com"
    rec = json.loads((tmp_path / "captures" / "proxy-svcs.jsonl").read_text().splitlines()[0])
    assert rec["request"]["body"]["text"] == "<soap/>"
    assert rec["request"]["headers"]["authorization"] == "<redacted>"
    assert json.loads(rec["response"]["body"]["text"]) == {"from": "logitech"}


def test_server_mode_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVER_MODE", "proxy")
    assert Settings().server_mode == "proxy"
