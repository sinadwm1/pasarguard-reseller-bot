from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "production"
    bot_token: SecretStr
    owner_telegram_ids: Annotated[list[int], NoDecode]
    admin_command: str = "/owner"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://pasarbot:pasarbot@postgres:5432/pasarbot"
    )
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    encryption_key: SecretStr
    callback_signing_key: SecretStr
    webhook_base_url: str | None = None
    webhook_secret: SecretStr | None = None
    receipt_group_id: int | None = None
    sync_interval_seconds: int = Field(default=300, ge=30, le=86400)
    quota_enforcement_mode: str = "suspend"
    expire_alert_days: Annotated[list[int], NoDecode] = [7, 3, 1]
    default_currency: str = "IRT"
    telegram_button_features: bool = True
    log_level: str = "INFO"

    @field_validator("owner_telegram_ids", mode="before")
    @classmethod
    def parse_owner_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("receipt_group_id", mode="before")
    @classmethod
    def empty_receipt_group_is_unset(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("expire_alert_days", mode="before")
    @classmethod
    def parse_alert_days(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("admin_command")
    @classmethod
    def validate_admin_command(cls, value: str) -> str:
        if not value.startswith("/") or " " in value:
            raise ValueError("ADMIN_COMMAND must be one Telegram command beginning with /")
        return value

    @field_validator("quota_enforcement_mode")
    @classmethod
    def validate_quota_mode(cls, value: str) -> str:
        if value not in {"alert", "suspend"}:
            raise ValueError("QUOTA_ENFORCEMENT_MODE must be alert or suspend")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
