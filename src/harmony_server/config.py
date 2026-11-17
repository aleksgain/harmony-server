"""Runtime settings.

Values come from (highest priority first): environment variables prefixed ``HARMONY_``,
a ``.env`` file, an optional TOML file named by ``HARMONY_CONFIG_FILE``, then defaults.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

#: Every Logitech hostname we answer for. Grouped by virtual host; see
#: ``middleware/hostname_routing.py``. The list of hostnames in Logitech's own service
#: directory (Discovery.svc GetAllServices capture) is the source for most of these.
DEFAULT_VHOSTS: dict[str, list[str]] = {
    "svcs": ["svcs.myharmony.com", "lb-svcs.myharmony.com", "cf-svcs.myharmony.com"],
    "setup": ["setup.myharmony.com"],
    "sl": ["sl.dhg.myharmony.com"],
    "content": ["content.dhg.myharmony.com", "files.myharmony.com"],
    "sus": ["sus.dhg.myharmony.com"],
    "telemetry": ["logging.dhg.myharmony.com", "home.myharmony.com"],
    # Logi ID (OIDC) - step 1 of Logitech's sign-in chain.
    "logi": ["accounts.logi.com", "id.logi.com"],
}


class _TomlSource(PydanticBaseSettingsSource):
    """Reads settings from the TOML file named by ``HARMONY_CONFIG_FILE`` (if any)."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        path = os.environ.get("HARMONY_CONFIG_FILE")
        self._data: dict[str, Any] = {}
        if path and Path(path).is_file():
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
            self._data = raw.get("harmony", raw)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if k in self.settings_cls.model_fields}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARMONY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- mode -----------------------------------------------------------------------
    #: ``standalone``: fully self-hosted. ``proxy``: relay every Logitech-host request to the
    #: real cloud and record request/response pairs to ``capture_dir`` (reverse engineering).
    #: Also read from the unprefixed ``SERVER_MODE`` env var.
    server_mode: Literal["standalone", "proxy"] = Field(
        default="standalone",
        validation_alias=AliasChoices("HARMONY_SERVER_MODE", "SERVER_MODE", "server_mode"),
    )
    #: Proxy mode: hostname -> real upstream IP. Needed because under the DNS override the
    #: Logitech names resolve to *us*; without an entry, the host's own resolver is used.
    proxy_upstream_ips: dict[str, str] = Field(default_factory=dict)
    proxy_timeout_seconds: float = 60.0
    proxy_verify_tls: bool = True

    # --- security -------------------------------------------------------------------
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 24 * 30

    # --- storage --------------------------------------------------------------------
    data_dir: Path = Path("./data")
    db_path: Path | None = None
    device_db_path: Path = Path("./device-db")

    # --- network --------------------------------------------------------------------
    bind_host: str = "0.0.0.0"
    http_port: int = 80
    https_port: int = 443
    enable_tls: bool = True
    cert_dir: Path | None = None
    #: Extra SANs (hostnames or IPs) for the generated server certificate.
    cert_extra_names: list[str] = Field(default_factory=list)

    # --- vhost routing --------------------------------------------------------------
    vhosts: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_VHOSTS))

    # --- what we tell hubs ----------------------------------------------------------
    #: ``discoveryServer`` handed to hubs. Logitech's real value is the Discovery.svc URL.
    discovery_server_url: str = "https://svcs.myharmony.com/Discovery/Discovery.svc"
    #: Value reported for ``discoveryServer`` in getProvisionInfo (the brief's form).
    provision_discovery_server: str = "https://svcs.myharmony.com"
    content_server_url: str = "https://content.dhg.myharmony.com"
    #: Substring rewrites applied to every URL in the Discovery.svc service directory.
    #: Useful for root-repointed hubs, e.g. {"https://svcs.myharmony.com": "https://harmony.lan"}.
    service_url_rewrites: dict[str, str] = Field(default_factory=dict)

    # --- behaviour ------------------------------------------------------------------
    #: Register unknown hubs that call getProvisionInfo (unclaimed until a user claims them).
    auto_register_hubs: bool = True
    #: Log every request we do not understand to ``capture_dir`` (JSONL) for reverse engineering.
    capture_unknown_requests: bool = True
    capture_dir: Path | None = None
    #: Redact these headers in captures.
    capture_redact_headers: list[str] = Field(
        default_factory=lambda: ["authorization", "cookie", "set-cookie"]
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _TomlSource(settings_cls),
            file_secret_settings,
        )

    # --- derived paths --------------------------------------------------------------
    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or self.data_dir / "harmony.db"

    @property
    def resolved_cert_dir(self) -> Path:
        return self.cert_dir or self.data_dir / "certs"

    @property
    def resolved_capture_dir(self) -> Path:
        return self.capture_dir or self.data_dir / "captures"

    def all_hostnames(self) -> list[str]:
        return sorted({h for hosts in self.vhosts.values() for h in hosts})


@lru_cache
def get_settings() -> Settings:
    return Settings()
