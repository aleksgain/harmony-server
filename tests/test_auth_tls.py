from __future__ import annotations

from pathlib import Path

from cryptography import x509
from fastapi.testclient import TestClient

from harmony_server.middleware.tls_intercept import ensure_certificates


def test_register_login_duplicate(client: TestClient) -> None:
    creds = {"email": "Carol@Example.com", "password": "correct horse"}
    assert client.post("/api/auth/register", json=creds).status_code == 201
    assert client.post("/api/auth/register", json=creds).status_code == 409
    r = client.post("/api/auth/login", json={**creds, "email": "carol@example.com"})
    assert r.status_code == 200 and r.json()["hubToken"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["email"] == "carol@example.com"
    assert (
        client.post("/api/auth/login", json={**creds, "password": "wrong password"}).status_code
        == 401
    )
    # hub-scoped token is not an API token
    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {r.json()['hubToken']}"}
        ).status_code
        == 401
    )


def test_certificates_cover_all_hosts(tmp_path: Path) -> None:
    hosts = ["svcs.myharmony.com", "setup.myharmony.com", "192.168.1.10"]
    paths = ensure_certificates(tmp_path, hosts)
    cert = x509.load_pem_x509_certificate(paths.server_cert.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"svcs.myharmony.com", "setup.myharmony.com"} <= set(
        san.get_values_for_type(x509.DNSName)
    )
    before = paths.server_cert.read_bytes()
    ensure_certificates(tmp_path, hosts)  # idempotent
    assert paths.server_cert.read_bytes() == before
    ensure_certificates(tmp_path, hosts + ["sl.dhg.myharmony.com"])  # new SAN -> reissue
    assert paths.server_cert.read_bytes() != before
