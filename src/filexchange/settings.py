"""File server configuration. Every value comes from the environment."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Short shared secrets are the whole attack. 32 urlsafe characters is 192 bits.
MIN_TOKEN_LENGTH = 32

GIB = 1024**3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Shared with the bot. Generate with: openssl rand -base64 48
    upload_token: SecretStr

    # How the bot and the browser reach this server, e.g. https://203.0.113.5:8443
    # Download links are built from it, so it must be what clients can resolve.
    public_url: str

    data_dir: Path = Path("/data")

    host: str = "0.0.0.0"  # noqa: S104 - it is a server; the container publishes one port
    port: int = 8443

    tls_cert: Path | None = None
    tls_key: Path | None = None
    # Refuses to serve without TLS unless this is set on purpose.
    allow_insecure: bool = False

    max_upload_bytes: int = 2 * GIB
    retention_hours: int = 24
    sweep_interval_seconds: int = 3600

    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("upload_token")
    @classmethod
    def _check_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_TOKEN_LENGTH:
            raise ValueError(f"upload token must be at least {MIN_TOKEN_LENGTH} characters")
        return value

    @field_validator("public_url")
    @classmethod
    def _check_public_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("public url must start with http:// or https://")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"unknown log level {value!r}")
        return level

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if (self.tls_cert is None) != (self.tls_key is None):
            raise ValueError("tls cert and key must be set together")
        if not self.tls_enabled and not self.allow_insecure:
            raise ValueError(
                "refusing to serve without TLS; set FX_TLS_CERT and FX_TLS_KEY, "
                "or FX_ALLOW_INSECURE=true if this really is a trusted network"
            )
        if self.tls_enabled and self.public_url.startswith("http://"):
            raise ValueError("TLS is on but the public url is http://; links would be insecure")
        if self.max_upload_bytes < 1:
            raise ValueError("max upload bytes must be positive")
        if self.retention_hours < 1:
            raise ValueError("retention hours must be at least 1")
        return self

    @property
    def tls_enabled(self) -> bool:
        return self.tls_cert is not None and self.tls_key is not None

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(self.uploads_dir, os.W_OK):
            raise ValueError(f"uploads dir {self.uploads_dir} is not writable")

    def link_for(self, token: str, filename: str) -> str:
        return f"{self.public_url}/d/{token}/{quote(filename)}"


def load_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
