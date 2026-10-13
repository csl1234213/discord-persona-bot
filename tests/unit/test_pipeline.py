from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import discord
import pytest

from app.core.config import Settings
from app.db.models import (
    AddressRule,
    CatchphraseRule,
    PersonalityProfile,
    Utterance,
    VocabularyRule,
)
from app.schemas.chat import ChatResult, DiscordContext, EvidenceItem, SelectionContext
from app.schemas.persona import PersonaCreate
from app.services.discord_context_resolver import DiscordContextResolver
from app.services.personality_resolver import PersonalityResolver
from app.services.utterance_selector import LexicalUtteranceSelector, normalized_tokens
from app.validators.boundary_validator import BoundaryValidator
from bot.adapters.interaction_adapter import context_from_interaction
from bot.adapters.message_adapter import (
    MessageRoute,
    content_without_mention,
    context_from_message,
    route_message,
    should_process_message,
)
from bot.adapters.response_renderer import (
    evidence_embed,
    neutralize_mentions,
    send_interaction_result,
    send_message_result,
    split_discord_text,
)


class StubUtterances:
    def __init__(self, items):
        self.items = items

    async def list_for_version(self, version_id, *, verified=None):
        return [item for item in self.items if item.verified is verified]


@pytest.mark.asyncio
async def test_selector_verified_filter_and_deterministic_ranking():
    version_id = uuid4()
    relevant = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="找工作不顺时先整理简历",
        topic="找工作",
        relationship="close_friend",
        audience_type="individual",
        verified=True,
        occurred_at=datetime.now(UTC),
    )
    unrelated = Utterance(id=uuid4(), persona_version_id=version_id, text="天气晴朗", verified=True)
    unverified = Utterance(
        id=uuid4(), persona_version_id=version_id, text="找工作找工作", verified=False
    )
    selector = LexicalUtteranceSelector(StubUtterances([unrelated, unverified, relevant]))
    context = SelectionContext(
        topic="找工作", relationship="close_friend", audience_type="individual"
    )
    first = await selector.select(
        message="找工作不顺", persona_version_id=version_id, context=context, limit=3
    )
    second = await selector.select(
        message="找工作不顺", persona_version_id=version_id, context=context, limit=3
    )
    assert first == second
    assert first.items[0].utterance_id == relevant.id
    assert all(item.utterance_id != unverified.id for item in first.items)
    assert normalized_tokens("工作 Work") == {"工作", "work"}
    assert {"天河", "河石", "石牌", "天河石", "河石牌", "天河石牌"}.issubset(
        normalized_tokens("天河石牌")
    )
    assert {"android8", "android", "8", "下载"}.issubset(normalized_tokens("Android8 download"))
    assert {"买日", "日货", "买日货"}.issubset(normalized_tokens("買日貨"))
    assert PersonaCreate(display_name="默认阈值人物").coverage_threshold == 0.05


@pytest.mark.asyncio
async def test_selector_rejects_non_adjacent_common_character_overlap():
    version_id = uuid4()
    unrelated = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="你先把事情说清楚，那我再告诉你该怎么办吧。",
        verified=True,
    )
    selector = LexicalUtteranceSelector(StubUtterances([unrelated]))
    result = await selector.select(
        message="吃屎吧你",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
    )
    assert result.items == []
    assert result.coverage_score == 0


@pytest.mark.asyncio
async def test_selector_accepts_continuous_meaningful_partial_phrase():
    version_id = uuid4()
    related = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="这条语料明确提到了石牌这个地点。",
        verified=True,
    )
    selector = LexicalUtteranceSelector(StubUtterances([related]))
    result = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
    )
    assert result.items[0].utterance_id == related.id
    assert result.coverage_score == 0.35
    assert "cjk_phrase_coverage" in result.items[0].reason_codes

    repeated = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
        recent_utterance_ids=[related.id],
    )
    assert repeated.items[0].utterance_id == related.id
    assert repeated.coverage_score == result.coverage_score


@pytest.mark.asyncio
async def test_selector_uses_bm25_to_prioritize_rare_keyword():
    version_id = uuid4()
    common = Utterance(id=uuid4(), persona_version_id=version_id, text="苹果", verified=True)
    rare = Utterance(id=uuid4(), persona_version_id=version_id, text="香蕉", verified=True)
    common_fillers = [
        Utterance(
            id=uuid4(),
            persona_version_id=version_id,
            text=f"苹果相关语料{index}",
            verified=True,
        )
        for index in range(4)
    ]
    selector = LexicalUtteranceSelector(StubUtterances([common, rare, *common_fillers]))
    result = await selector.select(
        message="苹果 香蕉",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
    )
    assert result.items[0].utterance_id == rare.id
    assert "bm25_keyword_rank" in result.items[0].reason_codes
    assert "hybrid_retrieval_fusion" in result.items[0].reason_codes
    assert result.coverage_score == 0.4


@pytest.mark.asyncio
async def test_hybrid_ranking_does_not_bypass_relevance_threshold():
    version_id = uuid4()
    weak = Utterance(id=uuid4(), persona_version_id=version_id, text="只提到石牌", verified=True)
    selector = LexicalUtteranceSelector(StubUtterances([weak]))
    result = await selector.select(
        message="从天河石牌火车站出发应该怎么换乘",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
    )
    assert result.items
    assert "hybrid_retrieval_fusion" in result.items[0].reason_codes
    assert result.coverage_score < 0.25


@pytest.mark.asyncio
async def test_screenshot_mixed_language_and_traditional_query_hits_raw_utterance():
    version_id = uuid4()
    android = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="測試手機Android8 download唔到示例檔案",
        verified=True,
    )
    goods = Utterance(id=uuid4(), persona_version_id=version_id, text="你買日貨", verified=True)
    selector = LexicalUtteranceSelector(StubUtterances([android, goods]))
    context = SelectionContext(discord_user_id="user-1")

    android_result = await selector.select(
        message="你的Android 8下载不了示例文件？",
        persona_version_id=version_id,
        context=context,
        limit=3,
        minimum_score=0.05,
    )
    assert android_result.items[0].utterance_id == android.id
    assert android_result.coverage_score >= 0.05

    goods_result = await selector.select(
        message="你买日货货",
        persona_version_id=version_id,
        context=context,
        limit=3,
        minimum_score=0.05,
    )
    assert goods_result.items[0].utterance_id == goods.id
    assert goods_result.coverage_score >= 0.05


@pytest.mark.asyncio
async def test_mandarin_query_matches_cantonese_corpus_without_rewriting_output():
    version_id = uuid4()
    why = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="你點解唔話我知？",
        verified=True,
    )
    doing = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="你喺度做乜嘢？",
        verified=True,
    )
    selector = LexicalUtteranceSelector(StubUtterances([why, doing]))
    context = SelectionContext(discord_user_id="user-1")

    why_result = await selector.select(
        message="你为什么不告诉我？",
        persona_version_id=version_id,
        context=context,
        limit=2,
        minimum_score=0.05,
    )
    assert why_result.items[0].utterance_id == why.id
    assert why_result.items[0].text == why.text

    doing_result = await selector.select(
        message="你在干什么？",
        persona_version_id=version_id,
        context=context,
        limit=2,
        minimum_score=0.05,
    )
    assert doing_result.items[0].utterance_id == doing.id
    assert doing_result.items[0].text == doing.text


@pytest.mark.asyncio
async def test_direct_keyword_strength_beats_freshness_and_hybrid_auxiliary_rank():
    version_id = uuid4()
    stronger = Utterance(id=uuid4(), persona_version_id=version_id, text="天河石", verified=True)
    weaker = Utterance(id=uuid4(), persona_version_id=version_id, text="天河", verified=True)
    selector = LexicalUtteranceSelector(StubUtterances([stronger, weaker]))
    result = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=2,
        recent_utterance_ids=[stronger.id],
        minimum_score=0.05,
    )
    assert result.items[0].utterance_id == stronger.id


@pytest.mark.asyncio
async def test_direct_mode_intent_fallback_is_random_with_recent_exclusion():
    version_id = uuid4()
    hostile = [
        Utterance(
            id=uuid4(),
            persona_version_id=version_id,
            text=text,
            verified=True,
        )
        for text in ("别在这里犯蠢", "这种垃圾说法不行", "滚开别烦我")
    ]
    statement = Utterance(
        id=uuid4(), persona_version_id=version_id, text="今天照常处理事情", verified=True
    )
    selector = LexicalUtteranceSelector(StubUtterances([*hostile, statement]))
    result = await selector.select(
        message="马来支",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
        recent_utterance_ids=[hostile[0].id, hostile[1].id],
        minimum_score=0.05,
        allow_intent_fallback=True,
    )
    assert result.items[0].utterance_id == hostile[2].id
    assert result.coverage_score == 0.0
    assert "intent_random_fallback" in result.items[0].reason_codes
    assert "requested_intent_hostile" in result.items[0].reason_codes


@pytest.mark.asyncio
async def test_question_intent_fallback_prefers_statement_response():
    version_id = uuid4()
    question = Utterance(
        id=uuid4(), persona_version_id=version_id, text="为什么要这样？", verified=True
    )
    statement = Utterance(
        id=uuid4(), persona_version_id=version_id, text="事情就是这样处理的。", verified=True
    )
    selector = LexicalUtteranceSelector(StubUtterances([question, statement]))
    result = await selector.select(
        message="有OpenAI暴露？",
        persona_version_id=version_id,
        context=SelectionContext(discord_user_id="user-1"),
        limit=3,
        minimum_score=0.05,
        allow_intent_fallback=True,
    )
    assert result.items[0].utterance_id == statement.id
    assert result.coverage_score == 0.0
    assert "requested_intent_question" in result.items[0].reason_codes


@pytest.mark.asyncio
async def test_selector_rotates_recent_equally_relevant_utterance():
    version_id = uuid4()
    first = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="天河石牌甲",
        verified=True,
    )
    second = Utterance(
        id=uuid4(),
        persona_version_id=version_id,
        text="天河石牌乙",
        verified=True,
    )
    selector = LexicalUtteranceSelector(StubUtterances([first, second]))
    context = SelectionContext(discord_user_id="user-1", guild_id="guild-1")
    initial = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=context,
        limit=2,
    )
    rotated = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=context,
        limit=2,
        recent_utterance_ids=[initial.items[0].utterance_id],
    )
    assert rotated.items[0].utterance_id != initial.items[0].utterance_id
    assert "recent_repeat_penalty" in rotated.items[1].reason_codes

    rotated_back = await selector.select(
        message="天河石牌",
        persona_version_id=version_id,
        context=context,
        limit=2,
        recent_utterance_ids=[
            rotated.items[0].utterance_id,
            initial.items[0].utterance_id,
        ],
    )
    assert rotated_back.items[0].utterance_id == initial.items[0].utterance_id


def test_discord_address_resolution_priority():
    version = uuid4()
    context = DiscordContext(
        guild_id="1",
        channel_id="2",
        discord_user_id="3",
        role_ids=["4"],
        relationship="close_friend",
        preferred_address="明确称呼",
    )
    rules = [
        AddressRule(
            id=uuid4(),
            persona_version_id=version,
            discord_user_id="3",
            address="用户称呼",
            priority=1,
        ),
        AddressRule(
            id=uuid4(),
            persona_version_id=version,
            discord_role_id="4",
            address="角色称呼",
            priority=20,
        ),
        AddressRule(
            id=uuid4(),
            persona_version_id=version,
            relationship="close_friend",
            address="朋友称呼",
            priority=30,
        ),
    ]
    resolver = DiscordContextResolver()
    assert resolver.resolve_address(context=context, rules=rules)[0] == "明确称呼"
    context.preferred_address = None
    assert resolver.resolve_address(context=context, rules=rules)[0] == "用户称呼"
    context.discord_user_id = "9"
    assert resolver.resolve_address(context=context, rules=rules)[0] == "角色称呼"
    context.role_ids = []
    assert resolver.resolve_address(context=context, rules=rules)[0] == "朋友称呼"
    context.relationship = "family"
    assert resolver.resolve_address(context=context, rules=[])[0] == "家人"
    context.relationship = None
    assert resolver.resolve_address(context=context, rules=[])[0] == "你"


def test_personality_merge_limits_and_invalid_values():
    profile = PersonalityProfile(persona_version_id=uuid4(), warmth=0.4, sarcasm=0.2)
    resolved = PersonalityResolver().resolve(
        profile,
        guild_defaults={"warmth": 0.6},
        session_overrides={"warmth": 0.9, "sarcasm": 0.9},
    )
    assert resolved.warmth == 0.9
    assert resolved.sources["warmth"] == "session"
    assert resolved.sarcasm == 0.6
    assert resolved.sources["sarcasm"] == "system_limit"
    with pytest.raises(ValueError, match="between 0 and 1"):
        PersonalityResolver().resolve(None, session_overrides={"warmth": 1.5})


def test_boundary_validator_all_deterministic_checks():
    version = uuid4()
    avoided = VocabularyRule(
        id=uuid4(),
        persona_version_id=version,
        text="保证成功",
        rule_type="avoided",
        weight=1,
        enabled=True,
    )
    catchphrase = CatchphraseRule(
        id=uuid4(),
        persona_version_id=version,
        text="先别急",
        max_per_response=1,
        max_per_conversation=2,
        enabled=True,
    )
    result = BoundaryValidator().validate(
        "我就是张三。他说：“保证成功”。system prompt。先别急，先别急。",
        persona_name="张三",
        vocabulary_rules=[avoided],
        catchphrase_rules=[catchphrase],
        conversation_text="先别急",
    )
    assert not result.valid
    assert "real_person_identity_claim" in result.reason_codes
    assert "named_identity_claim" in result.reason_codes
    assert "fabricated_attributed_quote" in result.reason_codes
    assert "INTERNAL_TERM_LEAK" in result.reason_codes
    assert any(code.startswith("catchphrase_limit") for code in result.reason_codes)
    assert any(code.startswith("conversation_catchphrase_limit") for code in result.reason_codes)
    assert (
        not BoundaryValidator()
        .validate("", persona_name="", vocabulary_rules=[], catchphrase_rules=[])
        .valid
    )


def message(
    *, dm=False, mentioned=False, author_bot=False, webhook=False, channel="20", guild="10"
):
    bot_user = SimpleNamespace(id=99)
    return SimpleNamespace(
        content="synthetic message",
        author=SimpleNamespace(id=30, bot=author_bot),
        webhook_id="hook" if webhook else None,
        guild=None if dm else SimpleNamespace(id=int(guild)),
        channel=SimpleNamespace(id=int(channel)),
        mentions=[bot_user] if mentioned else [],
    )


def test_message_routing_matches_single_guild_single_channel_matrix():
    settings = Settings(discord_guild_id="10", discord_chat_channel_id="20")
    assert not should_process_message(message(dm=True), bot_user_id=99, settings=settings)
    assert should_process_message(message(), bot_user_id=99, settings=settings)
    assert should_process_message(message(mentioned=True), bot_user_id=99, settings=settings)
    assert route_message(
        message(mentioned=True, channel="21"), bot_user_id=99, settings=settings
    ) == MessageRoute.REDIRECT
    assert not should_process_message(
        message(mentioned=True, channel="21"), bot_user_id=99, settings=settings
    )
    assert route_message(
        message(mentioned=True, guild="11"), bot_user_id=99, settings=settings
    ) == MessageRoute.IGNORE
    assert not should_process_message(
        message(mentioned=True, author_bot=True), bot_user_id=99, settings=settings
    )
    assert not should_process_message(
        message(mentioned=True, webhook=True), bot_user_id=99, settings=settings
    )
    assert not should_process_message(message(channel="21"), bot_user_id=99, settings=settings)
    assert not should_process_message(message(guild="11"), bot_user_id=99, settings=settings)
    assert content_without_mention("<@99> 你好 <@!99>", 99) == "你好"

    thread_message = message(channel="20")
    thread_message.channel = Mock(spec=discord.Thread)
    thread_message.channel.id = 20
    assert route_message(
        thread_message, bot_user_id=99, settings=settings
    ) == MessageRoute.IGNORE


def test_generated_mentions_are_neutralized_and_long_text_splits():
    text = "@everyone <@123> <@&456> @here"
    safe = neutralize_mentions(text)
    assert "@everyone" not in safe
    assert "<@123>" not in safe
    chunks = split_discord_text("第一段。" * 800, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_discord_context_adapters():
    role = SimpleNamespace(id=44)
    interaction = SimpleNamespace(
        guild_id=10,
        channel_id=20,
        channel=SimpleNamespace(id=20),
        user=SimpleNamespace(id=30, roles=[role]),
        id=40,
    )
    result = context_from_interaction(interaction)
    assert result.guild_id == "10"
    assert result.role_ids == ["44"]
    message_value = SimpleNamespace(
        guild=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=20),
        author=SimpleNamespace(id=30, roles=[role]),
        id=50,
    )
    result = context_from_message(message_value)
    assert result.discord_message_id == "50"
    assert result.channel_id == "20"


def chat_result(answer="回答"):
    personality = PersonalityResolver().resolve(None)
    return ChatResult(
        conversation_id=uuid4(),
        persona_id=uuid4(),
        persona_name="人物",
        persona_version="1.0.0",
        answer=answer,
        response_origin="CORPUS_COMPOSED",
        boundary_status="supported",
        coverage_score=0.8,
        resolved_address="你",
        evidence=[
            EvidenceItem(
                utterance_id=uuid4(),
                excerpt="证据摘录",
                score=0.8,
                reason_codes=["lexical_overlap"],
            )
        ],
        disclosure="模拟披露",
        effective_personality=personality,
    )


@pytest.mark.asyncio
async def test_response_renderers_use_safe_mentions_and_embeds():
    result = chat_result("@everyone 回答")
    embed = evidence_embed(result)
    assert embed.footer.text is None
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    await send_interaction_result(interaction, result, private=True, include_evidence=True)
    sent = interaction.followup.send.await_args
    assert "@everyone" not in sent.args[0]
    assert sent.kwargs["embed"] is not None
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)
    await send_message_result(
        SimpleNamespace(channel=channel, guild=SimpleNamespace(id=10), author=author), result
    )
    assert channel.send.await_args.args[0].startswith("<@42> ")
    allowed_mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users is False

    direct = chat_result("原始语料")
    direct.response_origin = "DIRECT_UTTERANCE"
    direct.selected_utterance_id = direct.evidence[0].utterance_id
    direct.debug["response_mode"] = "direct_utterance"
    direct_interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    await send_interaction_result(direct_interaction, direct, private=True, include_evidence=False)
    assert direct_interaction.followup.send.await_count == 1
    direct_channel = SimpleNamespace(send=AsyncMock())
    await send_message_result(
        SimpleNamespace(
            channel=direct_channel,
            guild=SimpleNamespace(id=10),
            author=SimpleNamespace(id=43),
        ),
        direct,
    )
    assert direct_channel.send.await_args.args[0].startswith("<@43> ")
    assert direct_channel.send.await_args.kwargs["embed"] is None

    composed_without_evidence = chat_result("正常生成回答")
    composed_without_evidence.evidence = []
    composed_without_evidence.coverage_score = 0.0
    generated_channel = SimpleNamespace(send=AsyncMock())
    await send_message_result(
        SimpleNamespace(
            channel=generated_channel,
            guild=SimpleNamespace(id=10),
            author=SimpleNamespace(id=45),
        ),
        composed_without_evidence,
    )
    assert generated_channel.send.await_args.args[0].startswith("<@45> ")
    assert generated_channel.send.await_args.kwargs["embed"] is None

    dm_channel = SimpleNamespace(send=AsyncMock())
    await send_message_result(
        SimpleNamespace(channel=dm_channel, guild=None, author=SimpleNamespace(id=44)), direct
    )
    assert dm_channel.send.await_args.args[0] == "原始语料"
    assert dm_channel.send.await_args.kwargs["allowed_mentions"].users is False
