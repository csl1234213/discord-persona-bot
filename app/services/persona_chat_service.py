import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import require_actor
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
    Utterance,
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
    SelectionResult,
)
from app.services.abrasive_style_policy import AbrasiveStylePolicy
from app.services.conversation_lock_manager import ConversationLockManager
from app.services.direct_utterance_gate import DirectUtteranceGate
from app.services.discord_context_resolver import DiscordContextResolver
from app.services.personality_resolver import TRAITS as PERSONALITY_TRAITS
from app.services.personality_resolver import PersonalityResolver
from app.services.political_corpus_retriever import PoliticalCorpusRetriever
from app.services.political_topic_router import PoliticalDecision, PoliticalTopicRouter
from app.services.privacy_service import PrivacyService
from app.services.prompt_builder import PromptBuilder
from app.services.resource_quota_service import ResourceQuotaService
from app.services.utterance_selector import LexicalUtteranceSelector
from app.validators.boundary_validator import BoundaryValidator

logger = logging.getLogger(__name__)
BOUNDARY_RESPONSE = "唔知喎。"
POLITICAL_FALLBACK_RESPONSE = "冇相符嘅原話，唔亂講。"


class PersonaChatService:
    def __init__(
        self,
        db: AsyncSession,
        llm_client: LLMClient,
        settings: Settings,
        semaphore: asyncio.Semaphore,
        conversation_locks: ConversationLockManager | None = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client
        self.settings = settings
        self.semaphore = semaphore
        self.conversation_locks = conversation_locks or ConversationLockManager()
        self.conversations = ConversationRepository(db)

    async def _active_persona(self, guild_id: str | None) -> tuple[Persona, GuildConfig | None]:
        if self.settings.release_security_mode and guild_id != self.settings.discord_guild_id:
            raise AppError("forbidden", "Persona scope is not authorized.")
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
            persona_version_id=persona.active_version_id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            discord_user_id=context.discord_user_id,
        )
        if conversation:
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
        self._guard_context(context)
        if not message.strip() or len(message) > self.settings.max_chat_input_chars:
            raise AppError("invalid_input", "Message is empty or exceeds the input limit.")
        try:
            message.encode("utf-8", errors="strict")
        except UnicodeError:
            raise AppError("invalid_input", "Message contains invalid Unicode.") from None
        key = (context.guild_id, context.channel_id, context.discord_user_id)
        try:
            async with self.conversation_locks.hold(key):
                return await self._chat_locked(message, context)
        except (SQLAlchemyError, OSError, TimeoutError):
            await self.db.rollback()
            raise AppError(
                "request_unavailable", "Storage or model temporarily unavailable."
            ) from None

    async def _chat_locked(self, message: str, context: DiscordContext) -> ChatResult:
        if context.discord_message_id is not None:
            existing = await self.db.scalar(
                select(DiscordMessageRecord.id)
                .where(DiscordMessageRecord.discord_message_id == context.discord_message_id)
                .limit(1)
            )
            if existing is not None:
                raise AppError("duplicate_event", "Duplicate request ignored.")
        if self.settings.release_security_mode:
            await self.db.commit()
            await ResourceQuotaService(self.db, self.settings).reserve(context, requests=1)
            await PrivacyService(self.db, self.settings).check_capacity()
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
        selection_context = SelectionContext(
            guild_id=context.guild_id,
            discord_user_id=context.discord_user_id,
            role_ids=context.role_ids,
            relationship=context.relationship,
            audience_type=context.audience_type,
        )
        selector = LexicalUtteranceSelector(UtteranceRepository(self.db))
        router = PoliticalTopicRouter()
        political_decision = router.route(message, history)
        if not self.settings.political_restrictions_enabled:
            political_decision = PoliticalDecision(
                is_political=False,
                reason_codes=("POLITICAL_RESTRICTIONS_DISABLED",),
            )
        selection_failed = False
        try:
            selection = await selector.select(
                message=message,
                persona_version_id=version.id,
                context=selection_context,
                limit=5,
                recent_utterance_ids=recent_utterance_ids,
                minimum_score=0.0,
                allow_intent_fallback=False,
            )
        except Exception:
            if not political_decision.is_political:
                raise
            logger.exception("Political selector failed closed")
            selection_failed = True
            selection = SelectionResult(items=[], coverage_score=0.0)
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
            "political_route": political_decision.is_political,
            "political_reason_codes": list(political_decision.reason_codes),
            "political_selection_failed_closed": selection_failed,
        }
        if self.settings.chat_direct_utterance_mode:
            direct = DirectUtteranceGate(router).decide(
                message, selection.items, political_decision
            )
            debug["direct_gate_reason_codes"] = list(direct.reason_codes)
            if direct.selected is not None:
                selected = direct.selected
                canonical = await self._canonical_utterance(selected.utterance_id, version.id)
                if canonical is None or canonical.text != selected.text:
                    debug["direct_gate_reason_codes"].append("DIRECT_REJECT_CANONICAL_INTEGRITY")
                else:
                    answer = canonical.text
                    debug["response_mode"] = "direct_utterance"
                    await self._save_answer(
                        conversation,
                        answer,
                        [str(canonical.id)],
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
                        [item for item in evidence if item.utterance_id == canonical.id],
                        personality,
                        "DIRECT_UTTERANCE",
                        canonical.id,
                        debug,
                    )

        if political_decision.is_political:
            retrieval = None
            if not selection_failed:
                try:
                    retrieval = await PoliticalCorpusRetriever(selector, router).retrieve(
                        message=message,
                        persona_version_id=version.id,
                        context=selection_context,
                        route=political_decision,
                        recent_utterance_ids=recent_utterance_ids,
                    )
                except Exception:
                    logger.exception("Political retrieval failed closed")
            if retrieval is None:
                debug["political_retrieval_reason_codes"] = ["POLITICAL_RETRIEVAL_ERROR_FALLBACK"]
            else:
                debug["political_retrieval_reason_codes"] = list(retrieval.reason_codes)
            selected = retrieval.selected if retrieval is not None else None
            canonical = (
                await self._canonical_utterance(selected.utterance_id, version.id)
                if selected is not None
                else None
            )
            if selected is None or canonical is None or canonical.text != selected.text:
                if selected is not None:
                    debug["political_retrieval_reason_codes"].append(
                        "POLITICAL_RETRIEVAL_CANONICAL_INTEGRITY"
                    )
                await self._save_answer(
                    conversation, POLITICAL_FALLBACK_RESPONSE, [], "safe_fallback"
                )
                return self._result(
                    conversation,
                    persona,
                    version,
                    POLITICAL_FALLBACK_RESPONSE,
                    "safe_fallback",
                    selection.coverage_score,
                    address,
                    [],
                    personality,
                    "SAFE_FALLBACK",
                    None,
                    debug,
                )
            answer = canonical.text
            await self._save_answer(
                conversation,
                answer,
                [str(canonical.id)],
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
                [
                    EvidenceItem(
                        utterance_id=canonical.id,
                        excerpt=canonical.text[:180],
                        score=selected.score,
                        reason_codes=selected.reason_codes,
                    )
                ],
                personality,
                "POLITICAL_CORPUS_UTTERANCE",
                canonical.id,
                debug,
            )

        if self.settings.release_security_mode and (
            not selection.items or selection.coverage_score < persona.coverage_threshold
        ):
            await self._save_answer(conversation, BOUNDARY_RESPONSE, [], "safe_fallback")
            return self._result(
                conversation,
                persona,
                version,
                BOUNDARY_RESPONSE,
                "safe_fallback",
                selection.coverage_score,
                address,
                [],
                personality,
                "SAFE_FALLBACK",
                None,
                debug,
            )

        style = await self.db.get(StyleProfile, version.id)
        if style is None:
            raise AppError("style_missing", "Persona style profile is missing.")
        catchphrases = await rules.catchphrases(version.id)
        vocabulary = await rules.vocabulary(version.id)
        style_decision = AbrasiveStylePolicy(self.settings).choose(
            conversation_id=str(conversation.id),
            turn_key=context.discord_message_id or str(len(previous)),
            user_message=message,
        )
        debug["abrasive_intensity"] = style_decision.intensity.value
        debug["abrasive_trigger_detected"] = style_decision.trigger_detected
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
            abrasive_intensity=style_decision.intensity.value,
            abrasive_style_descriptor=style_decision.abstract_descriptor,
        )
        validator = BoundaryValidator()
        conversation_text = "\n".join(item.content for item in previous if item.role == "assistant")
        failure_reasons: list[str] = []
        await self.db.commit()
        async with self.semaphore:
            for attempt in range(2):
                if self.settings.release_security_mode:
                    await ResourceQuotaService(self.db, self.settings).reserve_llm(context, request)
                    async with asyncio.timeout(self.settings.security_request_timeout_seconds):
                        answer = await self.llm_client.generate(request)
                else:
                    answer = await self.llm_client.generate(request)
                if not isinstance(answer, str) or len(answer) > 8000:
                    raise AppError("llm_invalid_response", "Model output exceeds the limit.")
                validation = validator.validate(
                    answer,
                    persona_name=persona.display_name,
                    vocabulary_rules=vocabulary,
                    catchphrase_rules=catchphrases,
                    conversation_text=conversation_text,
                    user_message=message,
                    response_origin="CORPUS_COMPOSED",
                    political_decision=political_decision,
                    abrasive_intensity=style_decision.intensity.value,
                    abrasive_trigger_detected=style_decision.trigger_detected,
                )
                if validation.valid:
                    await self._save_answer(
                        conversation,
                        answer,
                        [],
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
                        [],
                        personality,
                        "CORPUS_COMPOSED",
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
            [],
            personality,
            "SAFE_FALLBACK",
            None,
            debug,
        )

    async def reset(self, context: DiscordContext) -> int:
        self._guard_context(context)
        persona, _ = await self._active_persona(context.guild_id)
        count = await self.conversations.reset_for_user(
            persona_id=persona.id,
            persona_version_id=persona.active_version_id,
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
        self._guard_context(context)
        persona, _ = await self._active_persona(context.guild_id)
        conversation = await self.conversations.find_active(
            persona_id=persona.id,
            persona_version_id=persona.active_version_id,
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

    def _guard_context(self, context: DiscordContext) -> None:
        if self.settings.release_security_mode and (
            not self.settings.discord_guild_id
            or not self.settings.discord_chat_channel_id
            or context.guild_id != self.settings.discord_guild_id
            or context.channel_id != self.settings.discord_chat_channel_id
            or context.thread_id is not None
        ):
            raise AppError("forbidden", "Chat scope is not authorized.")
        if self.settings.release_security_mode:
            require_actor(self.db, context)

    async def _save_answer(
        self,
        conversation: DiscordConversation,
        answer: str,
        evidence_ids: list[str],
        boundary_status: str,
    ) -> None:
        if self.settings.release_security_mode:
            existing = await self.db.scalar(
                select(DiscordConversation.id).where(DiscordConversation.id == conversation.id)
            )
            if existing is None:
                raise AppError("conversation_erased", "Conversation no longer exists.")
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

    async def _canonical_utterance(
        self, utterance_id: UUID, persona_version_id: UUID
    ) -> Utterance | None:
        utterance = await UtteranceRepository(self.db).get(utterance_id)
        if (
            utterance is None
            or not utterance.verified
            or utterance.persona_version_id != persona_version_id
        ):
            return None
        return utterance

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
