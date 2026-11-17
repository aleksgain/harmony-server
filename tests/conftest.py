from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harmony_server.config import Settings
from harmony_server.main import create_app

FIXTURE_DB = Path(__file__).parent / "fixtures" / "device-db"
SVCS = {"Host": "svcs.myharmony.com"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        secret_key="test-secret-0123456789abcdef0123456789",
        data_dir=tmp_path,
        device_db_path=FIXTURE_DB,
        enable_tls=False,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def user(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/auth/register", json={"email": "alice@example.com", "password": "hunter22hunter"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def auth(user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}
