from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DISCLOSURE = "这是基于所提供材料生成的 AI 语言风格模拟，并非本人，也不代表本人当前观点。"


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Persona(TimestampMixin, Base):
    __tablename__ = "personas"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    disclosure: Mapped[str] = mapped_column(Text, default=DISCLOSURE)
    status: Mapped[str] = mapped_column(String(32), default="active")
    active_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", use_alter=True), nullable=True
    )
    coverage_threshold: Mapped[float] = mapped_column(Float, default=0.05)


class PersonaVersion(Base):
    __tablename__ = "persona_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    change_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Utterance(Base):
    __tablename__ = "utterances"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    audience_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scene: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emotion: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_type: Mapped[str] = mapped_column(String(80), default="authorized")
    source_reference: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UtteranceVoiceAsset(Base):
    __tablename__ = "utterance_voice_assets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    utterance_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("utterances.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    audio_relative_path: Mapped[str] = mapped_column(String(500), unique=True)
    text_sha256: Mapped[str] = mapped_column(String(64))
    audio_sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(100))
    file_extension: Mapped[str] = mapped_column(String(20))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VocabularyRule(Base):
    __tablename__ = "vocabulary_rules"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(String(240))
    rule_type: Mapped[str] = mapped_column(String(20))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    allowed_topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_positions: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CatchphraseRule(Base):
    __tablename__ = "catchphrase_rules"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(String(240))
    probability: Mapped[float] = mapped_column(Float, default=0.1)
    max_per_response: Mapped[int] = mapped_column(Integer, default=1)
    max_per_conversation: Mapped[int] = mapped_column(Integer, default=3)
    allowed_scenes: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AddressRule(Base):
    __tablename__ = "address_rules"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), index=True
    )
    guild_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    discord_user_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    discord_role_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    relationship: Mapped[str | None] = mapped_column(String(80), nullable=True)
    audience_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    age_relation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    formality: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str] = mapped_column(String(120))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class StyleProfile(Base):
    __tablename__ = "style_profiles"

    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), primary_key=True
    )
    sentence_length_preference: Mapped[str] = mapped_column(String(40), default="medium")
    paragraph_length_preference: Mapped[str] = mapped_column(String(40), default="medium")
    rhetorical_question_tendency: Mapped[float] = mapped_column(Float, default=0.2)
    punctuation_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    list_tendency: Mapped[float] = mapped_column(Float, default=0.2)
    emoji_policy: Mapped[str] = mapped_column(String(40), default="rare")
    opening_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    transition_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    closing_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)


class PersonalityProfile(Base):
    __tablename__ = "personality_profiles"

    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id", ondelete="CASCADE"), primary_key=True
    )
    directness: Mapped[float] = mapped_column(Float, default=0.5)
    warmth: Mapped[float] = mapped_column(Float, default=0.5)
    humor: Mapped[float] = mapped_column(Float, default=0.2)
    sarcasm: Mapped[float] = mapped_column(Float, default=0.1)
    patience: Mapped[float] = mapped_column(Float, default=0.5)
    empathy: Mapped[float] = mapped_column(Float, default=0.5)
    assertiveness: Mapped[float] = mapped_column(Float, default=0.5)
    skepticism: Mapped[float] = mapped_column(Float, default=0.3)
    formality: Mapped[float] = mapped_column(Float, default=0.5)
    verbosity: Mapped[float] = mapped_column(Float, default=0.5)
    emotional_intensity: Mapped[float] = mapped_column(Float, default=0.3)
    conflict_style: Mapped[str] = mapped_column(String(80), default="calm")


class DiscordConversation(TimestampMixin, Base):
    __tablename__ = "discord_conversations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    persona_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("personas.id"))
    persona_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("persona_versions.id")
    )
    guild_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(24), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    discord_user_id: Mapped[str] = mapped_column(String(24), index=True)
    relationship: Mapped[str | None] = mapped_column(String(80), nullable=True)
    audience_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    preferred_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    personality_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscordMessageRecord(Base):
    __tablename__ = "discord_message_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("discord_conversations.id", ondelete="CASCADE"), index=True
    )
    discord_message_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    selected_utterance_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    boundary_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GuildConfig(TimestampMixin, Base):
    __tablename__ = "guild_configs"

    guild_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    selected_persona_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("personas.id"), nullable=True
    )
    auto_reply_channel_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    personality_defaults: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
