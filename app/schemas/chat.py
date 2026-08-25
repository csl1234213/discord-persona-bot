from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.persona import PersonalityValues


class ChatModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DiscordContext(ChatModel):
    guild_id: str | None
    channel_id: str
    thread_id: str | None = None
    discord_user_id: str
    discord_message_id: str | None = None
    role_ids: list[str] = Field(default_factory=list)
    relationship: str | None = None
    audience_type: str = "individual"
    preferred_address: str | None = None
    personality_overrides: dict[str, float] = Field(default_factory=dict)


class SelectionContext(ChatModel):
    guild_id: str | None = None
    discord_user_id: str | None = None
    role_ids: list[str] = Field(default_factory=list)
    topic: str | None = None
    scene: str | None = None
    relationship: str | None = None
    audience_type: str | None = None


class SelectedUtterance(ChatModel):
    utterance_id: UUID
    text: str
    context: str
    score: float
    reason_codes: list[str]


class SelectionResult(ChatModel):
    items: list[SelectedUtterance]
    coverage_score: float


class HistoryMessage(ChatModel):
    role: Literal["user", "assistant"]
    content: str


class ResolvedPersonality(PersonalityValues):
    sources: dict[str, Literal["published_default", "guild", "session", "system_limit"]]


class PersonaGenerationRequest(ChatModel):
    system_prompt: str
    user_message: str
    selected_utterances: list[SelectedUtterance]
    history: list[HistoryMessage]
    resolved_address: str
    effective_personality: ResolvedPersonality
    corrective_instruction: str | None = None


class EvidenceItem(ChatModel):
    utterance_id: UUID
    excerpt: str
    score: float
    reason_codes: list[str]


class ChatResult(ChatModel):
    conversation_id: UUID
    persona_id: UUID
    persona_name: str
    persona_version: str
    answer: str
    response_origin: Literal["DIRECT_UTTERANCE", "LLM_GENERATED", "SAFE_FALLBACK"]
    selected_utterance_id: UUID | None = None
    boundary_status: Literal["supported", "insufficient_evidence", "safe_fallback"]
    coverage_score: float
    resolved_address: str
    evidence: list[EvidenceItem]
    disclosure: str
    effective_personality: ResolvedPersonality
    debug: dict = Field(default_factory=dict)
