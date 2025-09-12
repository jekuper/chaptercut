"""Application settings. Every value comes from the environment or .env."""

from __future__ import annotations

import os
import re
import shlex
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

TOKEN_RE = re.compile(r"^\d{8,10}:[A-Za-z0-9_-]{35}$")

MultiDelivery = Literal["zip", "individual", "both"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr
    bot_api_url: str = "http://bot-api:8081"
    bot_api_local: bool = True

    allowed_user_ids: Annotated[list[int], NoDecode, Field(min_length=1)]
    admin_user_ids: Annotated[list[int], NoDecode] = []

    data_dir: Path = Path("/data")
    cookies_file: Path | None = None

    ytdlp_extra_args: str = ""

    audio_bitrate: str = ""
    audio_multi_delivery: MultiDelivery = "zip"
    cover_square: bool = True

    max_send_bytes: int = 1_900_000_000
    cache_max_bytes: int = 21_474_836_480
    worker_concurrency: int = 1
    download_timeout_seconds: int = 1800
    shutdown_grace_seconds: int = 120
    rate_limit_per_minute: int = 20

    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("allowed_user_ids", "admin_user_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> list[int]:
        # NoDecode keeps env values as raw strings; direct construction may
        # still pass a list, and a single int is accepted for convenience.
        if value is None:
            return []
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        if isinstance(value, int) and not isinstance(value, bool):
            return [value]
        if isinstance(value, list):
            return [int(item) for item in cast(list[Any], value)]
        raise TypeError("user id list must be a comma-separated string or a list of ints")

    @field_validator("bot_token")
    @classmethod
    def _check_token(cls, value: SecretStr) -> SecretStr:
        if not TOKEN_RE.match(value.get_secret_value()):
            raise ValueError("bot token must look like <8-10 digits>:<35 urlsafe chars>")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"unknown log level {value!r}")
        return level

    @field_validator("audio_bitrate")
    @classmethod
    def _check_bitrate(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^\d{2,3}[Kk]?$", value):
            raise ValueError("audio bitrate must look like 192K")
        return value.upper()

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        unknown = set(self.admin_user_ids) - set(self.allowed_user_ids)
        if unknown:
            raise ValueError(f"admin user ids must also be allowed: {sorted(unknown)}")
        if self.worker_concurrency < 1:
            raise ValueError("worker concurrency must be at least 1")
        if self.bot_api_local and self.bot_api_url.startswith("https://api.telegram.org"):
            raise ValueError("bot_api_local must be false when using Telegram's cloud server")
        return self

    @cached_property
    def ytdlp_extra_arg_list(self) -> list[str]:
        return shlex.split(self.ytdlp_extra_args)

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chaptercut.db"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    def active_cookies_file(self) -> Path | None:
        """The cookies file, but only if it actually exists and is readable."""
        path = self.cookies_file
        if path is None or not path.is_file():
            return None
        return path

    def ensure_dirs(self) -> None:
        """Create the runtime directory layout and verify it is writable."""
        for path in (self.data_dir, self.cache_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not os.access(self.data_dir, os.W_OK):
            raise ValueError(f"data dir {self.data_dir} is not writable")


def load_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
