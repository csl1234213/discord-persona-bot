import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import (
    DiscordConversation,
    DiscordMessageRecord,
    GuildConfig,
    Persona,
    PersonalityProfile,
    PersonaVersion,
    StyleProfile,
)
from app.llm.base import LLMClient
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.guild_repository import GuildRepository
from app.repositories.persona_repository import PersonaRepository
from app.repositories.rule_repository import RuleRepository
from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.chat import (
    ChatResult,
    DiscordContext,
    EvidenceItem,
    HistoryMessage,
    SelectionContext,
)
from app.services.discord_context_resolver import DiscordContextResolver
from app.services.personality_resolver import TRAITS as PERSONALITY_TRAITS
from app.services.personality_resolver import PersonalityResolver
from app.services.prompt_builder import PromptBuilder
from app.services.utterance_selector import LexicalUtteranceSelector
from app.validators.boundary_validator import BoundaryValidator

logger = logging.getLogger(__name__)
BOUNDARY_RESPONSE = "现有言论不足以判断这个人会怎样回应这个问题。"


class PersonaChatService:
    def __init__(
        self,
        db: AsyncSession,
        llm_client: LLMClient,
        settings: Settings,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self.db = db
        self.llm_client = llm_client
        self.settings = settings
        self.semaphore = semaphore
        self.conversations = ConversationRepository(db)

    async def _active_persona(self, guild_id: str | None) -> tuple[Persona, GuildConfig | None]:
        guild_config = await GuildRepository(self.db).get(guild_id) if guild_id else None
        persona = (
            await PersonaRepository(self.db).get(guild_config.selected_persona_id)
            if guild_config and guild_config.selected_persona_id
            else None
        )
        if persona is None:
            personas = await PersonaRepository(self.db).list()
            persona = next((item for item in personas if item.active_version_id), None)
        if persona is None or persona.active_version_id is None:
            raise AppError("persona_unavailable", "No published persona is configured.")
        return persona, guild_config

    async def _conversation(self, persona: Persona, context: DiscordContext) -> DiscordConversation:
        conversation = await self.conversations.find_active(
            persona_id=persona.id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            discord_user_id=context.discord_user_id,
        )
        if conversation:
            if conversation.persona_version_id != persona.active_version_id:
                conversation.persona_version_id = persona.active_version_id
                await self.db.flush()
            return conversation
        conversation = DiscordConversation(
            persona_id=persona.id,
            persona_version_id=persona.active_version_id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            thread_id=context.thread_id,
            discord_user_id=context.discord_user_id,
            relationship=context.relationship,
            audience_type=context.audience_type,
            preferred_address=context.preferred_address,
            personality_overrides=context.personality_overrides,
        )
        return await self.conversations.add(conversation)

    async def chat(self, message: str, context: DiscordContext) -> ChatResult:
        persona, guild_config = await self._active_persona(context.guild_id)
        conversation = await self._conversation(persona, context)
        version = await self.db.get(PersonaVersion, conversation.persona_version_id)
        if version is None or version.status != "published":
            raise AppError("pinned_version_unavailable", "Pinned persona version is unavailable.")
        previous = await self.conversations.messages(
            conversation.id, limit=self.settings.max_history_messages
        )
        history = [
            HistoryMessage(role=item.role, content=item.content)
            for item in previous
            if item.role in {"user", "assistant"}
        ]
        recent_utterance_ids: list[UUID] = []
        for item in reversed(previous):
            if item.role != "assistant":
                continue
            for utterance_id in item.selected_utterance_ids or []:
                try:
                    recent_utterance_ids.append(UUID(str(utterance_id)))
                except (AttributeError, TypeError, ValueError):
                    logger.warning("Ignoring malformed historical utterance id")
                if len(recent_utterance_ids) >= self.settings.chat_recent_utterance_window:
                    break
            if len(recent_utterance_ids) >= self.settings.chat_recent_utterance_window:
                break
        self.db.add(
            DiscordMessageRecord(
                conversation_id=conversation.id,
                discord_message_id=context.discord_message_id,
                role="user",
                content=message,
            )
        )
        await self.db.flush()
        selection = await LexicalUtteranceSelector(UtteranceRepository(self.db)).select(
            message=message,
            persona_version_id=version.id,
            context=SelectionContext(
                guild_id=context.guild_id,
                discord_user_id=context.discord_user_id,
                role_ids=context.role_ids,
                relationship=context.relationship,
                audience_type=context.audience_type,
            ),
            limit=5,
            recent_utterance_ids=recent_utterance_ids,
            minimum_score=persona.coverage_threshold,
            allow_intent_fallback=self.settings.chat_direct_utterance_mode,
        )
        rules = RuleRepository(self.db)
        address_rules = await rules.addresses(version.id)
        address, address_source = DiscordContextResolver().resolve_address(
            context=context, rules=address_rules
        )
        profile = await self.db.get(PersonalityProfile, version.id)
        personality = PersonalityResolver().resolve(
            profile,
            guild_defaults=guild_config.personality_defaults if guild_config else None,
            session_overrides={
                **(conversation.personality_overrides or {}),
                **context.personality_overrides,
            },
        )
        evidence = [
            EvidenceItem(
                utterance_id=item.utterance_id,
                excerpt=item.text[:180],
                score=item.score,
                reason_codes=item.reason_codes,
            )
            for item in selection.items
        ]
        debug = {
            "address_source": address_source,
            "personality_sources": personality.sources,
            "selector_reason_codes": [item.reason_codes for item in selection.items],
            "recent_utterance_count": len(recent_utterance_ids),
        }
        intent_fallback = bool(
            selection.items and "intent_random_fallback" in selection.items[0].reason_codes
        )
        if intent_fallback:
            debug["selection_mode"] = "intent_random_fallback"
            debug["effective_coverage_threshold"] = 0.0
        if not selection.items or (
            not intent_fallback and selection.coverage_score < persona.coverage_threshold
        ):
            await self._save_answer(conversation, BOUNDARY_RESPONSE, [], "insufficient_evidence")
            return self._result(
                conversation,
                persona,
                version,
                BOUNDARY_RESPONSE,
                "insufficient_evidence",
                selection.coverage_score,
                address,
                evidence,
                personality,
                "SAFE_FALLBACK",
                None,
                debug,
            )

        if self.settings.chat_direct_utterance_mode:
            selected = selection.items[0]
            answer = selected.text.strip()
            debug["response_mode"] = "direct_utterance"
            await self._save_answer(
                conversation,
                answer,
                [str(selected.utterance_id)],
                "supported",
            )
            return self._result(
                conversation,
                persona,
                version,
                answer,
                "supported",
                selection.coverage_score,
                address,
                evidence,
                personality,
                "DIRECT_UTTERANCE",
                selected.utterance_id,
                debug,
            )

        style = await self.db.get(StyleProfile, version.id)
        if style is None:
            raise AppError("style_missing", "Persona style profile is missing.")
        catchphrases = await rules.catchphrases(version.id)
        vocabulary = await rules.vocabulary(version.id)
        request = PromptBuilder().build(
            disclosure=persona.disclosure,
            style=style,
            personality=personality,
            resolved_address=address,
            relationship=context.relationship,
            evidence=selection.items,
            history=history,
            user_message=message,
            catchphrases=catchphrases,
        )
        validator = BoundaryValidator()
        conversation_text = "\n".join(item.content for item in previous if item.role == "assistant")
        failure_reasons: list[str] = []
        async with self.semaphore:
            for attempt in range(2):
                answer = await self.llm_client.generate(request)
                validation = validator.validate(
                    answer,
                    persona_name=persona.display_name,
                    vocabulary_rules=vocabulary,
                    catchphrase_rules=catchphrases,
                    conversation_text=conversation_text,
                )
                if validation.valid:
                    await self._save_answer(
                        conversation,
                        answer,
                        [str(item.utterance_id) for item in selection.items],
                        "supported",
                    )
                    return self._result(
                        conversation,
                        persona,
                        version,
                        answer,
                        "supported",
                        selection.coverage_score,
                        address,
                        evidence,
                        personality,
                        "LLM_GENERATED",
                        None,
                        debug,
                    )
                failure_reasons = validation.reason_codes
                request.corrective_instruction = "Correct safety failures: " + ", ".join(
                    failure_reasons
                )
                logger.warning(
                    "Generation validation failed attempt=%s reasons=%s",
                    attempt + 1,
                    failure_reasons,
                )
        debug["validation_failure_reasons"] = failure_reasons
        await self._save_answer(conversation, BOUNDARY_RESPONSE, [], "safe_fallback")
        return self._result(
            conversation,
            persona,
            version,
            BOUNDARY_RESPONSE,
            "safe_fallback",
            selection.coverage_score,
            address,
            evidence,
            personality,
            "SAFE_FALLBACK",
            None,
            debug,
        )

    async def reset(self, context: DiscordContext) -> int:
        persona, _ = await self._active_persona(context.guild_id)
        count = await self.conversations.reset_for_user(
            persona_id=persona.id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            discord_user_id=context.discord_user_id,
        )
        await self.db.commit()
        return count

    async def persona_info(self, guild_id: str | None) -> tuple[Persona, PersonaVersion]:
        persona, _ = await self._active_persona(guild_id)
        version = await self.db.get(PersonaVersion, persona.active_version_id)
        if version is None:
            raise AppError("persona_unavailable", "No published persona is configured.")
        return persona, version

    async def update_personality(
        self, context: DiscordContext, values: dict[str, float]
    ) -> DiscordConversation:
        persona, _ = await self._active_persona(context.guild_id)
        conversation = await self.conversations.find_active(
            persona_id=persona.id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            discord_user_id=context.discord_user_id,
        )
        if conversation is None:
            raise AppError("conversation_missing", "Start a chat before changing session settings.")
        for key, value in values.items():
            if key not in PERSONALITY_TRAITS or not 0 <= value <= 1:
                raise AppError("invalid_personality", "Invalid personality setting.")
        conversation.personality_overrides = {
            **(conversation.personality_overrides or {}),
            **values,
        }
        await self.db.commit()
        return conversation

    async def _save_answer(
        self,
        conversation: DiscordConversation,
        answer: str,
        evidence_ids: list[str],
        boundary_status: str,
    ) -> None:
        conversation.last_active_at = datetime.now(UTC)
        self.db.add(
            DiscordMessageRecord(
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
                selected_utterance_ids=evidence_ids,
                boundary_status=boundary_status,
            )
        )
        await self.db.flush()
        await self.conversations.prune_messages(
            conversation.id,
            keep=self.settings.max_history_messages,
            cutoff=datetime.now(UTC) - timedelta(days=self.settings.conversation_retention_days),
        )
        await self.db.commit()

    def _result(
        self,
        conversation: DiscordConversation,
        persona: Persona,
        version: PersonaVersion,
        answer: str,
        status: str,
        coverage: float,
        address: str,
        evidence: list[EvidenceItem],
        personality,
        response_origin: str,
        selected_utterance_id: UUID | None,
        debug: dict,
    ) -> ChatResult:
        return ChatResult(
            conversation_id=conversation.id,
            persona_id=persona.id,
            persona_name=persona.display_name,
            persona_version=version.version,
            answer=answer,
            response_origin=response_origin,
            selected_utterance_id=selected_utterance_id,
            boundary_status=status,
            coverage_score=coverage,
            resolved_address=address,
            evidence=evidence,
            disclosure=persona.disclosure,
            effective_personality=personality,
            debug=debug,
        )
