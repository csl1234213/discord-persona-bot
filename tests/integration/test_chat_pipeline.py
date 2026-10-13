import asyncio

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import DiscordConversation, DiscordMessageRecord
from app.llm.fake import FakeLLMClient
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.chat import DiscordContext
from app.schemas.persona import PersonaCreate, UtteranceCreate, VersionCreate
from app.services.conversation_lock_manager import ConversationLockManager
from app.services.persona_chat_service import BOUNDARY_RESPONSE, PersonaChatService
from app.services.persona_update_service import PersonaUpdateService


class TrackingLLM:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    async def generate(self, request):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return "你，继续做能控制的事情。"


class OrderedBlockingLLM:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            self.entered.set()
            await self.release.wait()
            return "你，继续做能控制的事情。"
        return "你，先把眼前能做的做好。"


async def seed(db, *, threshold=0.25):
    service = PersonaUpdateService(db)
    persona, version = await service.create_persona(
        PersonaCreate(display_name="示例人物", coverage_threshold=threshold)
    )
    utterance = await service.add_utterance(
        persona.id,
        version.id,
        UtteranceCreate(
            text="找工作不顺的时候，先把能控制的事情做好。",
            topic="找工作",
            relationship="close_friend",
            audience_type="individual",
            verified=True,
        ),
    )
    await service.publish(persona.id, version.id)
    return persona, version, utterance


def context(user="100", channel="200"):
    return DiscordContext(
        guild_id="300",
        channel_id=channel,
        discord_user_id=user,
        relationship="close_friend",
        role_ids=[],
    )


def chat_service(db, fake, *, max_concurrent=2, direct=False):
    settings = Settings(
        llm_provider="fake",
        max_history_messages=12,
        max_concurrent_llm_calls=max_concurrent,
        chat_direct_utterance_mode=direct,
    )
    return PersonaChatService(db, fake, settings, asyncio.Semaphore(max_concurrent))


@pytest.mark.asyncio
async def test_nonpolitical_without_evidence_is_composed(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed(db, threshold=0.6)
        fake = FakeLLMClient()
        result = await chat_service(db, fake).chat("量子物理是什么？", context())
        assert result.boundary_status == "supported"
        assert result.response_origin == "CORPUS_COMPOSED"
        assert result.selected_utterance_id is None
        assert result.answer != BOUNDARY_RESPONSE
        assert result.disclosure
        assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_composed_prompt_includes_disclosure_excludes_raw_corpus(db_sessionmaker):
    async with db_sessionmaker() as db:
        _, _, utterance = await seed(db)
        fake = FakeLLMClient()
        ctx = context()
        ctx.preferred_address = "阿李"
        ctx.personality_overrides = {"sarcasm": 1.0, "warmth": 0.8}
        result = await chat_service(db, fake).chat("找工作不顺怎么办？", ctx)
        assert result.boundary_status == "supported"
        assert result.response_origin == "CORPUS_COMPOSED"
        assert result.selected_utterance_id is None
        assert result.resolved_address == "阿李"
        assert result.effective_personality.sarcasm == 0.6
        assert result.evidence == []
        assert str(utterance.id) not in fake.calls[0].system_prompt
        assert utterance.text not in fake.calls[0].system_prompt
        assert result.disclosure in fake.calls[0].system_prompt
        assert "SIMULATION DISCLOSURE" in fake.calls[0].system_prompt
        assert "tone controls only" in fake.calls[0].system_prompt.lower()


@pytest.mark.asyncio
async def test_direct_utterance_mode_returns_raw_evidence_without_llm(db_sessionmaker):
    async with db_sessionmaker() as db:
        _, _, utterance = await seed(db)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            f"请重复这句：{utterance.text}", context(user="direct")
        )
        assert result.boundary_status == "supported"
        assert result.answer == utterance.text
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert result.selected_utterance_id == utterance.id
        assert result.debug["response_mode"] == "direct_utterance"
        assert fake.calls == []


@pytest.mark.asyncio
async def test_two_character_overlap_replays_full_corpus_text(db_sessionmaker):
    async with db_sessionmaker() as db:
        update = PersonaUpdateService(db)
        persona, version = await update.create_persona(
            PersonaCreate(display_name="混合语言测试人物", coverage_threshold=0.05)
        )
        utterance = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(
                text="測試手機Android8 download唔到示例檔案",
                verified=True,
            ),
        )
        await update.publish(persona.id, version.id)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            "你的Android 8下载不了示例文件？", context(user="mixed-language")
        )
        assert result.boundary_status == "supported"
        assert result.answer == utterance.text
        assert result.coverage_score >= 0.05
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert fake.calls == []


@pytest.mark.asyncio
async def test_full_corpus_sentence_embedded_in_message_replays_canonical_text(db_sessionmaker):
    async with db_sessionmaker() as db:
        _, _, utterance = await seed(db)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            f"我想问下：{utterance.text}，你点睇？", context(user="embedded-sentence")
        )
        assert result.answer == utterance.text
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert result.selected_utterance_id == utterance.id
        assert fake.calls == []


@pytest.mark.asyncio
async def test_traditional_corpus_matches_simplified_message(db_sessionmaker):
    async with db_sessionmaker() as db:
        update = PersonaUpdateService(db)
        persona, version = await update.create_persona(
            PersonaCreate(display_name="繁简测试人物")
        )
        utterance = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text="國安食屎國高科技", verified=True),
        )
        await update.publish(persona.id, version.id)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            "国安食屎国高科技，你点睇？", context(user="traditional-simplified")
        )
        assert result.answer == utterance.text
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert fake.calls == []


@pytest.mark.asyncio
async def test_near_complete_keyword_sentence_with_one_omitted_character_replays(
    db_sessionmaker,
):
    async with db_sessionmaker() as db:
        update = PersonaUpdateService(db)
        persona, version = await update.create_persona(
            PersonaCreate(display_name="近似句测试人物")
        )
        utterance = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text="反白紙反漢獨，香港人加油！", verified=True),
        )
        await update.publish(persona.id, version.id)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            "反白紙漢獨，香港人加油", context(user="near-complete")
        )
        assert result.answer == utterance.text
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert fake.calls == []


@pytest.mark.asyncio
async def test_substantial_sentence_prefix_replays_full_corpus_text(db_sessionmaker):
    async with db_sessionmaker() as db:
        update = PersonaUpdateService(db)
        persona, version = await update.create_persona(
            PersonaCreate(display_name="长句前缀测试人物")
        )
        utterance = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text="你係埋全家人癲咗病咗啊？有精神病入青山醫！", verified=True),
        )
        await update.publish(persona.id, version.id)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            "你係埋全家人癫咗病咗啊", context(user="substantial-prefix")
        )
        assert result.answer == utterance.text
        assert result.response_origin == "DIRECT_UTTERANCE"
        assert fake.calls == []


@pytest.mark.asyncio
async def test_direct_mode_without_keyword_uses_nonpolitical_composer(db_sessionmaker):
    async with db_sessionmaker() as db:
        _, _, utterance = await seed(db, threshold=0.05)
        fake = FakeLLMClient()
        result = await chat_service(db, fake, direct=True).chat(
            "有OpenAI暴露？", context(user="intent-fallback")
        )
        assert result.boundary_status == "supported"
        assert result.answer != utterance.text
        assert result.response_origin == "CORPUS_COMPOSED"
        assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_direct_mode_rejects_ambiguous_equal_candidates(db_sessionmaker):
    async with db_sessionmaker() as db:
        update = PersonaUpdateService(db)
        persona, version = await update.create_persona(
            PersonaCreate(display_name="轮换测试人物", coverage_threshold=0.25)
        )
        first = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text="天河石牌甲", verified=True),
        )
        second = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text="天河石牌乙", verified=True),
        )
        await update.publish(persona.id, version.id)

        fake = FakeLLMClient()
        service = chat_service(db, fake, direct=True)
        user_context = context(user="rotation-user")
        answers = [
            await service.chat("天河石牌", user_context),
            await service.chat("天河石牌", user_context),
            await service.chat("天河石牌", user_context),
        ]

        assert all(answer.response_origin == "CORPUS_COMPOSED" for answer in answers)
        assert all(answer.answer not in {first.text, second.text} for answer in answers)
        assert len(fake.calls) == 3

        other_user = await service.chat("天河石牌", context(user="other-user"))
        assert other_user.debug["recent_utterance_count"] == 0


@pytest.mark.asyncio
async def test_one_retry_then_success_and_second_failure_fallback(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed(db)
        fake = FakeLLMClient(["我就是示例人物。", "你，继续做能控制的事情。"])
        result = await chat_service(db, fake).chat("找工作不顺怎么办？", context(user="1"))
        assert result.boundary_status == "supported"
        assert len(fake.calls) == 2
        assert "identity" in fake.calls[1].corrective_instruction

        failing = FakeLLMClient(["我就是示例人物。", "我就是示例人物。"])
        result = await chat_service(db, failing).chat("找工作不顺怎么办？", context(user="2"))
        assert result.boundary_status == "safe_fallback"
        assert result.response_origin == "SAFE_FALLBACK"
        assert result.selected_utterance_id is None
        assert result.answer == BOUNDARY_RESPONSE
        assert len(failing.calls) == 2


@pytest.mark.asyncio
async def test_active_version_change_starts_a_pinned_session_and_reset_only_caller(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, _, _ = await seed(db)
        fake = FakeLLMClient()
        service = chat_service(db, fake)
        first_result = await service.chat("找工作不顺怎么办？", context(user="1"))
        other_result = await service.chat("找工作不顺怎么办？", context(user="2"))

        update = PersonaUpdateService(db)
        second = await update.create_draft(persona.id, VersionCreate(version="2.0.0"))
        await update.publish(persona.id, second.id)
        upgraded = await service.chat("找工作不顺怎么办？", context(user="1"))
        assert upgraded.persona_version == second.version
        assert upgraded.conversation_id != first_result.conversation_id

        old_session = await db.get(DiscordConversation, first_result.conversation_id)
        assert old_session.persona_version_id != second.id

        assert await service.reset(context(user="1")) == 1
        replacement = await service.chat("找工作不顺怎么办？", context(user="1"))
        assert replacement.conversation_id != first_result.conversation_id
        assert replacement.persona_version == "2.0.0"
        other_session = await db.get(DiscordConversation, other_result.conversation_id)
        assert other_session is not None
        other = await ConversationRepository(db).find_active(
            persona_id=persona.id,
            persona_version_id=other_session.persona_version_id,
            guild_id="300",
            channel_id="200",
            discord_user_id="2",
        )
        assert other is not None
        assert other.id == other_result.conversation_id
        assert other.persona_version_id != second.id


@pytest.mark.asyncio
async def test_shared_concurrency_semaphore_limits_model_calls(db_sessionmaker):
    async with db_sessionmaker() as setup_db:
        await seed(setup_db)
    tracker = TrackingLLM()
    semaphore = asyncio.Semaphore(1)
    settings = Settings(llm_provider="fake", max_concurrent_llm_calls=1)
    async with db_sessionmaker() as first_db, db_sessionmaker() as second_db:
        first = PersonaChatService(first_db, tracker, settings, semaphore)
        second = PersonaChatService(second_db, tracker, settings, semaphore)
        await asyncio.gather(
            first.chat("找工作不顺怎么办？", context(user="10")),
            second.chat("找工作不顺怎么办？", context(user="11")),
        )
    assert tracker.maximum == 1


@pytest.mark.asyncio
async def test_slow_turn_releases_sqlite_and_orders_same_conversation(db_sessionmaker):
    async with db_sessionmaker() as setup_db:
        await seed(setup_db)

    llm = OrderedBlockingLLM()
    settings = Settings(llm_provider="fake", max_concurrent_llm_calls=1)
    semaphore = asyncio.Semaphore(1)
    locks = ConversationLockManager()
    shared_context = context(user="ordered-user", channel="ordered-channel")
    async with db_sessionmaker() as first_db, db_sessionmaker() as second_db:
        first_service = PersonaChatService(first_db, llm, settings, semaphore, locks)
        second_service = PersonaChatService(second_db, llm, settings, semaphore, locks)
        first = asyncio.create_task(first_service.chat("第一条问题", shared_context))
        await llm.entered.wait()
        assert first_db.in_transaction() is False

        async with db_sessionmaker() as inspect_db:
            persisted = list(await inspect_db.scalars(select(DiscordMessageRecord)))
            assert [record.role for record in persisted] == ["user"]

        second = asyncio.create_task(second_service.chat("第二条问题", shared_context))
        await asyncio.sleep(0.05)
        assert not second.done()
        async with db_sessionmaker() as inspect_db:
            persisted = list(await inspect_db.scalars(select(DiscordMessageRecord)))
            assert [record.role for record in persisted] == ["user"]

        llm.release.set()
        await asyncio.gather(first, second)

    async with db_sessionmaker() as inspect_db:
        persisted = list(
            await inspect_db.scalars(
                select(DiscordMessageRecord).order_by(DiscordMessageRecord.created_at)
            )
        )
    assert [record.role for record in persisted] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [item.role for item in llm.calls[1].history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_many_concurrent_users_all_complete_direct_responses(db_sessionmaker):
    async with db_sessionmaker() as setup_db:
        _, _, utterance = await seed(setup_db, threshold=0.05)

    async def request(user_number: int):
        async with db_sessionmaker() as db:
                return await chat_service(db, FakeLLMClient(), direct=True).chat(
                    f"请重复这句：{utterance.text}",
                context(user=f"concurrent-{user_number}"),
            )

    user_count = 25
    results = await asyncio.gather(*(request(index) for index in range(user_count)))

    assert len(results) == user_count
    assert all(result.boundary_status == "supported" for result in results)
    assert all(result.answer == utterance.text for result in results)
    assert len({result.conversation_id for result in results}) == user_count


@pytest.mark.asyncio
async def test_persisted_history_is_pruned_to_configured_limit(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed(db)
        settings = Settings(llm_provider="fake", max_history_messages=2)
        service = PersonaChatService(db, FakeLLMClient(), settings, asyncio.Semaphore(1))
        first = await service.chat("找工作不顺怎么办？", context(user="history"))
        await service.chat("找工作不顺还能怎么办？", context(user="history"))

        messages = await ConversationRepository(db).messages(first.conversation_id, limit=10)
        assert len(messages) == 2
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[0].content == "找工作不顺还能怎么办？"
