from functools import cached_property, lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _snowflake_set(value: str | set[str] | list[str] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    return {str(item).strip() for item in value if str(item).strip()}


class Settings(BaseSettings):
    discord_bot_token: SecretStr | None = None
    discord_application_id: str | None = None
    discord_test_guild_id: str | None = None
    discord_owner_user_ids: Annotated[set[str], NoDecode] = set()
    discord_allowed_guild_ids: Annotated[set[str], NoDecode] = set()
    discord_auto_reply_channel_ids: Annotated[set[str], NoDecode] = set()
    discord_enable_message_content_intent: bool = False
    discord_sync_commands_on_startup: bool = True
    bot_smoke_test_mode: bool = False
    chat_direct_utterance_mode: bool = False
    voice_asset_feature_enabled: bool = False
    voice_asset_root: Path | None = None
    voice_button_timeout_seconds: int = 300
    database_url: str = "sqlite+aiosqlite:///./data/persona_bot.db"
    llm_provider: Literal["fake", "openai_compatible"] = "fake"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4.1-mini"
    log_level: str = "INFO"
    max_import_bytes: int = 1_048_576
    max_import_lines: int = 5_000
    max_history_messages: int = 12
    chat_recent_utterance_window: int = 3
    conversation_retention_days: int = 30
    user_cooldown_seconds: float = 3.0
    guild_cooldown_seconds: float = 0.0
    max_concurrent_chat_requests: int = 8
    max_concurrent_llm_calls: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator(
        "discord_owner_user_ids",
        "discord_allowed_guild_ids",
        "discord_auto_reply_channel_ids",
        mode="before",
    )
    @classmethod
    def parse_snowflakes(cls, value):
        return _snowflake_set(value)

    @field_validator(
        "max_import_bytes",
        "max_import_lines",
        "max_history_messages",
        "chat_recent_utterance_window",
        "conversation_retention_days",
        "voice_button_timeout_seconds",
        "max_concurrent_chat_requests",
        "max_concurrent_llm_calls",
    )
    @classmethod
    def positive_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit must be positive")
        return value

    @cached_property
    def application_id_int(self) -> int | None:
        return int(self.discord_application_id) if self.discord_application_id else None

    def ensure_local_directories(self) -> None:
        prefix = "sqlite+aiosqlite:///./"
        if self.database_url.startswith(prefix):
            Path(self.database_url.removeprefix(prefix)).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
