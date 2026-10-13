import re
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _snowflake_set(value: str | set[str] | list[str] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    return {str(item).strip() for item in value if str(item).strip()}


class Settings(BaseSettings):
    discord_bot_token: SecretStr | None = None
    discord_bot_token_file: Path | None = None
    release_security_mode: bool = False
    max_chat_input_chars: int = Field(default=4000, ge=1, le=16000)
    max_pending_chat_requests: int = Field(default=32, ge=1, le=256)
    security_user_daily_requests: int = Field(default=100, ge=1, le=10000)
    security_global_daily_requests: int = Field(default=1000, ge=1, le=100000)
    security_user_daily_tokens: int = Field(default=200000, ge=1, le=10000000)
    security_global_daily_tokens: int = Field(default=2000000, ge=1, le=100000000)
    security_user_daily_cost_micros: int = Field(default=2000000, ge=1)
    security_global_daily_cost_micros: int = Field(default=20000000, ge=1)
    security_price_micros_per_token: int | None = Field(default=None, ge=0)
    security_max_prompt_bytes: int = Field(default=32768, ge=256, le=131072)
    security_request_timeout_seconds: float = Field(default=120, ge=1, le=300)
    security_retention_days: int = Field(default=7, ge=1, le=30)
    security_cleanup_interval_seconds: int = Field(default=300, ge=10, le=3600)
    security_max_conversations: int = Field(default=2000, ge=1, le=10000)
    security_max_messages: int = Field(default=20000, ge=1, le=100000)
    security_database_max_bytes: int = Field(default=268435456, ge=1048576, le=1073741824)
    discord_application_id: str | None = None
    discord_test_guild_id: str | None = None
    discord_guild_id: str | None = None
    discord_chat_channel_id: str | None = None
    discord_owner_user_ids: Annotated[set[str], NoDecode] = set()
    discord_allowed_guild_ids: Annotated[set[str], NoDecode] = set()
    discord_auto_reply_channel_ids: Annotated[set[str], NoDecode] = set()
    discord_enable_message_content_intent: bool = False
    discord_sync_commands_on_startup: bool = True
    bot_smoke_test_mode: bool = False
    chat_direct_utterance_mode: bool = False
    political_restrictions_enabled: bool = True
    voice_asset_feature_enabled: bool = False
    voice_asset_root: Path | None = None
    voice_button_timeout_seconds: int = 300
    database_url: str = "sqlite+aiosqlite:///./data/persona_bot.db"
    llm_provider: Literal["fake", "openai_compatible"] = "fake"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_reasoning_effort: str | None = None
    llm_max_tokens: int | None = None
    llm_temperature: float = 0.3
    llm_top_p: float | None = None
    llm_ollama_native: bool = False
    llm_timeout_seconds: float = 30.0
    llm_fallback_enabled: bool = False
    llm_fallback_base_url: str | None = None
    llm_fallback_api_key: SecretStr | None = None
    llm_fallback_model: str | None = None
    llm_fallback_timeout_seconds: float = 480.0
    llm_fallback2_enabled: bool = False
    llm_fallback2_base_url: str | None = None
    llm_fallback2_api_key: SecretStr | None = None
    llm_fallback2_model: str | None = None
    llm_fallback2_timeout_seconds: float = 480.0
    llm_failover_cooldown_seconds: float = 60.0
    persona_abrasive_style_enabled: bool = True
    persona_abrasive_normal_weight: int = 70
    persona_abrasive_teasing_weight: int = 20
    persona_abrasive_abrasive_weight: int = 10
    log_level: str = "INFO"
    max_import_bytes: int = 1_048_576
    max_import_lines: int = 5_000
    max_history_messages: int = Field(
        default=20,
        validation_alias=AliasChoices("MAX_CONVERSATION_MESSAGES", "MAX_HISTORY_MESSAGES"),
    )
    chat_recent_utterance_window: int = 3
    conversation_retention_days: int = 30
    user_cooldown_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices("USER_CHAT_COOLDOWN_SECONDS", "USER_COOLDOWN_SECONDS"),
    )
    mention_redirect_cooldown_seconds: float = 10.0
    guild_cooldown_seconds: float = 0.0
    max_concurrent_chat_requests: int = 8
    max_concurrent_llm_calls: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
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
        "discord_application_id",
        "discord_test_guild_id",
        "discord_guild_id",
        "discord_chat_channel_id",
        mode="before",
    )
    @classmethod
    def validate_discord_snowflake(cls, value):
        if value is None or value == "":
            return None
        normalized = str(value).strip()
        if not re.fullmatch(r"[0-9]{1,20}", normalized) or int(normalized) <= 0:
            raise ValueError("Discord IDs must be positive decimal snowflakes")
        return normalized

    @field_validator("llm_reasoning_effort", "llm_max_tokens", "llm_top_p", mode="before")
    @classmethod
    def empty_optional_llm_values(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "llm_fallback_base_url",
        "llm_fallback_api_key",
        "llm_fallback_model",
        "llm_fallback2_base_url",
        "llm_fallback2_api_key",
        "llm_fallback2_model",
        mode="before",
    )
    @classmethod
    def empty_optional_fallback_values(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("llm_max_tokens")
    @classmethod
    def positive_optional_llm_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("LLM max tokens must be positive")
        return value

    @field_validator("llm_temperature")
    @classmethod
    def valid_llm_temperature(cls, value: float) -> float:
        if not 0 <= value <= 2:
            raise ValueError("LLM temperature must be between 0 and 2")
        return value

    @field_validator("llm_top_p")
    @classmethod
    def valid_optional_llm_top_p(cls, value: float | None) -> float | None:
        if value is not None and not 0 < value <= 1:
            raise ValueError("LLM top-p must be greater than 0 and at most 1")
        return value

    @field_validator(
        "llm_timeout_seconds", "llm_fallback_timeout_seconds", "llm_fallback2_timeout_seconds"
    )
    @classmethod
    def positive_llm_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("LLM timeout must be positive")
        return value

    @field_validator("llm_failover_cooldown_seconds")
    @classmethod
    def positive_llm_failover_cooldown(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("LLM failover cooldown must be positive")
        return value

    @model_validator(mode="after")
    def load_token_file(self):
        if self.discord_bot_token_file is not None:
            if self.discord_bot_token is not None:
                raise ValueError("Configure only DISCORD_BOT_TOKEN_FILE or DISCORD_BOT_TOKEN")
            try:
                with self.discord_bot_token_file.open("r", encoding="utf-8") as handle:
                    token = handle.read(4097).strip()
                if not token or len(token) > 4096:
                    raise ValueError
            except (OSError, UnicodeError, ValueError):
                raise ValueError("DISCORD_BOT_TOKEN_FILE must contain a readable token") from None
            self.discord_bot_token = SecretStr(token)
        return self

    @model_validator(mode="after")
    def valid_llm_fallback(self):
        if not self.llm_fallback_enabled:
            return self
        if self.llm_provider != "openai_compatible":
            raise ValueError("LLM fallback requires the openai_compatible provider")
        if self.llm_fallback_base_url is None:
            raise ValueError("LLM fallback base URL is required when fallback is enabled")
        if self.llm_fallback_api_key is None:
            raise ValueError("LLM fallback API key is required when fallback is enabled")
        primary = self.llm_base_url.rstrip("/").casefold()
        fallback = self.llm_fallback_base_url.rstrip("/").casefold()
        fallback_model = self.llm_fallback_model or self.llm_model
        if primary == fallback and self.llm_model == fallback_model:
            raise ValueError("LLM primary and fallback base URLs must differ")
        if self.llm_fallback2_enabled:
            if self.llm_fallback2_base_url is None:
                raise ValueError("LLM second fallback base URL is required when enabled")
            if self.llm_fallback2_api_key is None:
                raise ValueError("LLM second fallback API key is required when enabled")
        return self

    @field_validator(
        "persona_abrasive_normal_weight",
        "persona_abrasive_teasing_weight",
        "persona_abrasive_abrasive_weight",
    )
    @classmethod
    def nonnegative_abrasive_weights(cls, value: int) -> int:
        if value < 0:
            raise ValueError("abrasive style weights must be nonnegative")
        return value

    @field_validator("persona_abrasive_abrasive_weight")
    @classmethod
    def positive_abrasive_weight_total(cls, value: int, info) -> int:
        total = (
            int(info.data.get("persona_abrasive_normal_weight", 70))
            + int(info.data.get("persona_abrasive_teasing_weight", 20))
            + value
        )
        if total <= 0:
            raise ValueError("at least one abrasive style weight must be positive")
        return value

    @field_validator(
        "max_import_bytes",
        "max_import_lines",
        "max_history_messages",
        "chat_recent_utterance_window",
        "conversation_retention_days",
        "voice_button_timeout_seconds",
        "mention_redirect_cooldown_seconds",
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

    def validate_production_discord(self) -> None:
        missing = [
            name
            for name, value in (
                ("DISCORD_GUILD_ID", self.discord_guild_id),
                ("DISCORD_CHAT_CHANNEL_ID", self.discord_chat_channel_id),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(f"Required Discord configuration missing: {', '.join(missing)}")
        if not self.discord_enable_message_content_intent:
            raise RuntimeError(
                "DISCORD_ENABLE_MESSAGE_CONTENT_INTENT=true is required for dedicated-channel mode."
            )
        if not self.discord_owner_user_ids:
            raise RuntimeError("Required Discord configuration missing: DISCORD_OWNER_USER_IDS")
        if self.llm_provider != "fake":
            if self.release_security_mode and self.security_price_micros_per_token is None:
                raise RuntimeError(
                    "SECURITY_PRICE_MICROS_PER_TOKEN requires verified provider pricing"
                )
            if not self.llm_api_key or not self.llm_base_url or not self.llm_model:
                raise RuntimeError("Required LLM configuration missing")
            if self.llm_max_tokens is None or self.llm_max_tokens > 2048:
                raise RuntimeError("LLM_MAX_TOKENS must be set between 1 and 2048")


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        raise RuntimeError("Invalid configuration fields: " + ", ".join(fields)) from None
