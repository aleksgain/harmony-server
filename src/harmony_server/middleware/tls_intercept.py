"""TLS helpers for the DNS-rewrite setup.

The hub and the legacy app connect to ``https://*.myharmony.com``. With a local DNS
override those names resolve to us, so we need a certificate for them. We generate a
private CA plus one server certificate whose SANs cover every hostname we answer for.

Whether a given client accepts it is a separate question (see ``docs/tls-setup.md``):

* Desktop / browser / new client: trust ``ca.crt`` in the OS store - works.
* Harmony Hub stock firmware: it validates against its own built-in CA bundle (assumed;
  unverified). A private CA will most likely be rejected unless the hub is rooted and the
  CA is added to its bundle, or the hub's discoveryServer is repointed (hub-root-repoint.md).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)

CA_CN = "Harmony Revival Local CA"


@dataclass(frozen=True)
class CertPaths:
    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path


def cert_paths(cert_dir: Path) -> CertPaths:
    return CertPaths(
        cert_dir / "ca.crt", cert_dir / "ca.key", cert_dir / "server.crt", cert_dir / "server.key"
    )


def _key() -> rsa.RSAPrivateKey:
    # RSA-2048 rather than EC: old embedded TLS stacks (hub firmware) may lack EC support.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def _san_entries(names: list[str]) -> list[x509.GeneralName]:
    out: list[x509.GeneralName] = []
    for n in names:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            out.append(x509.DNSName(n))
    return out


def ensure_certificates(cert_dir: Path, hostnames: list[str], force: bool = False) -> CertPaths:
    """Create CA + server cert if missing (or if the SAN list changed)."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    paths = cert_paths(cert_dir)
    now = dt.datetime.now(dt.UTC)
    names = sorted(set(hostnames) | {"localhost", "127.0.0.1"})

    if force or not (paths.ca_cert.exists() and paths.ca_key.exists()):
        ca_key = _key()
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_CN)])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    content_commitment=False, key_encipherment=False, data_encipherment=False,
                    key_agreement=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write_key(paths.ca_key, ca_key)
        paths.ca_cert.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        force = True  # new CA => reissue leaf
        log.info("Generated new local CA at %s", paths.ca_cert)

    if not force and paths.server_cert.exists() and paths.server_key.exists():
        existing = x509.load_pem_x509_certificate(paths.server_cert.read_bytes())
        try:
            san = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(v) for v in san.get_values_for_type(x509.DNSName)} | {
                str(v) for v in san.get_values_for_type(x509.IPAddress)
            }
        except x509.ExtensionNotFound:
            have = set()
        if have >= set(names) and existing.not_valid_after_utc > now + dt.timedelta(days=30):
            return paths

    ca_key_loaded = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)
    assert isinstance(ca_key_loaded, rsa.RSAPrivateKey)
    ca_cert_loaded = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
    key = _key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "svcs.myharmony.com")]))
        .issuer_name(ca_cert_loaded.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        # <= 397 days so Apple/Chrome accept it once the CA is trusted.
        .not_valid_after(now + dt.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName(_san_entries(names)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key_loaded.public_key()),
            critical=False,
        )
        .sign(ca_key_loaded, hashes.SHA256())
    )
    _write_key(paths.server_key, key)
    # Full chain so clients that lack the CA locally still get it presented.
    paths.server_cert.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + ca_cert_loaded.public_bytes(serialization.Encoding.PEM)
    )
    log.info("Issued server certificate for %d names", len(names))
    return paths
