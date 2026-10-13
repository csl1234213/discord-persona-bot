import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.llm.fake import FakeLLMClient
from app.schemas.chat import DiscordContext
from app.services.persona_chat_service import POLITICAL_FALLBACK_RESPONSE, PersonaChatService
from benchmarks.phase_2a02_political_fixture import (
    POLITICAL_CORPUS as CORPUS,
)
from benchmarks.phase_2a02_political_fixture import (
    load_political_policy_cases,
    seed_political_corpus,
)

CASES = load_political_policy_cases()


class BlockingLLM:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def generate(self, _request) -> str:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return "唔知喎，睇下先啦。"


def context(case_id):
    return DiscordContext(
        guild_id="phase-2a02",
        channel_id="routing",
        discord_user_id=case_id,
        discord_message_id=f"message-{case_id}",
    )


def service(db, llm, *, direct):
    settings = Settings(
        llm_provider="fake",
        chat_direct_utterance_mode=direct,
        persona_abrasive_style_enabled=False,
    )
    return PersonaChatService(db, llm, settings, asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_all_political_routes_are_exact_or_fallback_and_never_call_llm(db_sessionmaker):
    async with db_sessionmaker() as db:
        utterances = await seed_political_corpus(db)
        for case in CASES["political_retrieval_cases"]:
            fake = FakeLLMClient()
            result = await service(db, fake, direct=case["kind"] == "direct").chat(
                case["input"], context(case["id"])
            )
            assert fake.calls == []
            if case["kind"] == "unsupported":
                assert result.response_origin == "SAFE_FALLBACK"
                assert result.answer == POLITICAL_FALLBACK_RESPONSE
                assert result.selected_utterance_id is None
            else:
                expected = utterances[case["corpus_key"]]
                expected_origin = (
                    "DIRECT_UTTERANCE"
                    if case["kind"] == "direct"
                    else "POLITICAL_CORPUS_UTTERANCE"
                )
                assert result.response_origin == expected_origin, (case["id"], result.debug)
                assert result.selected_utterance_id == expected.id
                assert result.answer == expected.text
                if case["id"] == "unsupported-05":
                    assert "POLITICAL_RETRIEVAL_SINGLE_CONTEXT_FAMILY" in result.debug[
                        "political_retrieval_reason_codes"
                    ]


@pytest.mark.asyncio
async def test_nonpolitical_composition_has_one_repair_and_no_raw_corpus(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        fake = FakeLLMClient(["我是 AI。", "唔知喎，睇下先啦。"])
        result = await service(db, fake, direct=False).chat(
            "早晨，你今日心情点啊？", context("ordinary-repair")
        )
        assert result.response_origin == "CORPUS_COMPOSED"
        assert len(fake.calls) == 2
        assert "AI_IDENTITY_LEAK" in fake.calls[1].corrective_instruction
        assert fake.calls[0].selected_utterances == []
        assert all(text not in fake.calls[0].system_prompt for text in CORPUS.values())


@pytest.mark.asyncio
async def test_unsupported_personal_fact_second_failure_is_safe_fallback(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        fake = FakeLLMClient(["我有一个弟弟。", "我收入好多。"])
        result = await service(db, fake, direct=False).chat(
            "你有兄弟姐妹吗？", context("unsupported-fallback")
        )
        assert result.response_origin == "SAFE_FALLBACK"
        assert result.answer == "唔知喎。"
        assert len(fake.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direct_mode", "expected_origin"),
    [(False, "POLITICAL_CORPUS_UTTERANCE"), (True, "DIRECT_UTTERANCE")],
)
async def test_political_exact_match_never_calls_llm_in_either_direct_mode(
    db_sessionmaker, direct_mode, expected_origin
):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        fake = FakeLLMClient()
        result = await service(db, fake, direct=direct_mode).chat(
            CORPUS["hk_law"], context(f"direct-mode-{direct_mode}")
        )
        assert result.response_origin == expected_origin
        assert result.answer == CORPUS["hk_law"]
        assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_mode", [False, True])
async def test_taiwan_keyword_family_returns_exact_corpus_without_llm(
    db_sessionmaker, direct_mode
):
    async with db_sessionmaker() as db:
        utterances = await seed_political_corpus(db)
        fake = FakeLLMClient()
        result = await service(db, fake, direct=direct_mode).chat(
            "两岸统一政策争议你点睇？",
            context(f"taiwan-keyword-family-{direct_mode}"),
        )
        expected = utterances["tw_sovereignty"]
        assert result.response_origin == "POLITICAL_CORPUS_UTTERANCE"
        assert result.answer == expected.text
        assert result.selected_utterance_id == expected.id
        assert "POLITICAL_RETRIEVAL_SINGLE_CONTEXT_FAMILY" in result.debug[
            "political_retrieval_reason_codes"
        ]
        assert fake.calls == []


@pytest.mark.asyncio
async def test_political_retrieval_exception_fails_closed_without_llm(
    db_sessionmaker, monkeypatch
):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        monkeypatch.setattr(
            "app.services.persona_chat_service.PoliticalCorpusRetriever.retrieve",
            AsyncMock(side_effect=RuntimeError("forced retrieval failure")),
        )
        fake = FakeLLMClient()
        result = await service(db, fake, direct=False).chat(
            "香港国安法点睇啊？", context("retrieval-failure")
        )
        assert result.response_origin == "SAFE_FALLBACK"
        assert result.answer == POLITICAL_FALLBACK_RESPONSE
        assert fake.calls == []


@pytest.mark.asyncio
async def test_political_selector_exception_fails_closed_without_llm(
    db_sessionmaker, monkeypatch
):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        monkeypatch.setattr(
            "app.services.persona_chat_service.LexicalUtteranceSelector.select",
            AsyncMock(side_effect=RuntimeError("forced selector failure")),
        )
        fake = FakeLLMClient()
        result = await service(db, fake, direct=True).chat(
            "香港国安法点睇啊？", context("selector-failure")
        )
        assert result.response_origin == "SAFE_FALLBACK"
        assert result.answer == POLITICAL_FALLBACK_RESPONSE
        assert fake.calls == []


@pytest.mark.asyncio
async def test_political_canonical_integrity_failure_falls_back_without_llm(
    db_sessionmaker, monkeypatch
):
    async with db_sessionmaker() as db:
        await seed_political_corpus(db)
        monkeypatch.setattr(
            PersonaChatService,
            "_canonical_utterance",
            AsyncMock(return_value=None),
        )
        fake = FakeLLMClient()
        result = await service(db, fake, direct=True).chat(
            CORPUS["hk_law"], context("canonical-failure")
        )
        assert result.response_origin == "SAFE_FALLBACK"
        assert result.answer == POLITICAL_FALLBACK_RESPONSE
        assert fake.calls == []


@pytest.mark.asyncio
async def test_political_request_does_not_wait_for_slow_nonpolitical_llm(db_sessionmaker):
    async with db_sessionmaker() as setup_db:
        await seed_political_corpus(setup_db)

    llm = BlockingLLM()
    settings = Settings(
        llm_provider="fake",
        chat_direct_utterance_mode=False,
        persona_abrasive_style_enabled=False,
        max_concurrent_llm_calls=1,
    )
    semaphore = asyncio.Semaphore(1)
    async with db_sessionmaker() as ordinary_db, db_sessionmaker() as political_db:
        ordinary_service = PersonaChatService(ordinary_db, llm, settings, semaphore)
        political_service = PersonaChatService(political_db, llm, settings, semaphore)
        ordinary = asyncio.create_task(
            ordinary_service.chat("早晨，你今日心情点啊？", context("slow-ordinary"))
        )
        await llm.entered.wait()
        political = asyncio.create_task(
            political_service.chat(
                "两岸统一政策争议你点睇？", context("fast-political")
            )
        )
        try:
            political_result = await asyncio.wait_for(asyncio.shield(political), timeout=2)
        finally:
            llm.release.set()
            await ordinary
        assert political_result.response_origin == "POLITICAL_CORPUS_UTTERANCE"
        assert llm.calls == 1
