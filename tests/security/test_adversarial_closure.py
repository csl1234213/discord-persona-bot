"""Network-free provider, routing, Unicode and storage adversarial tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.core.authorization import bind_actor
from app.core.config import Settings
from app.core.errors import AppError
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from app.schemas.chat import DiscordContext
from app.services.persona_chat_service import PersonaChatService
from bot.adapters.response_renderer import neutralize_mentions, split_discord_text
from bot.cogs.diagnostics import DiagnosticsCog
from bot.views.voice_asset_view import PublicVoiceAssetButton


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (429, b"private-provider-error"),
        (500, b"private-provider-error"),
        (200, b"not json"),
        (200, b"x" * 140000),
        (302, b""),
    ],
    ids=["429", "500", "invalid-json", "oversized-body", "redirect"],
)
async def test_release_provider_bounded_failures_no_redirects(monkeypatch, status, body):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, content=body, headers={"Location": "https://other.invalid"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    policy = Settings(
        _env_file=None,
        release_security_mode=True,
        llm_api_key="synthetic-test-key",
        llm_base_url="https://provider.invalid/v1",
        llm_max_tokens=128,
    )
    request = SimpleNamespace(
        system_prompt="synthetic style",
        corrective_instruction=None,
        history=[],
        user_message="synthetic message",
    )
    with pytest.raises(AppError) as caught:
        await OpenAICompatibleLLMClient(policy).generate(request)
    assert len(calls) == 1
    assert "private-provider-error" not in str(caught.value)
    assert "synthetic-test-key" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        "@everyone",
        "@here",
        "<@123>",
        "<@!123>",
        "<@&123>",
        "```@everyone\n```",
        "@everyone\u200b",
        "🇭🇰" * 3000,
    ],
    ids=["everyone", "here", "user", "nickname", "role", "markdown", "zero-width", "long-emoji"],
)
def test_mentions_and_long_unicode_chunks(payload):
    result = neutralize_mentions(payload)
    assert "@everyone" not in result and "@here" not in result
    chunks = split_discord_text(result)
    assert all(len(chunk) <= 1900 for chunk in chunks)
    for chunk in chunks:
        chunk.encode("utf-8", errors="strict")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError(), OperationalError("synthetic", {}, Exception("private-error"))]
)
async def test_chat_provider_storage_failure_sanitized_without_retry(
    db_sessionmaker, monkeypatch, failure
):
    async with db_sessionmaker() as db:
        policy = Settings(
            _env_file=None,
            release_security_mode=True,
            discord_guild_id="10",
            discord_chat_channel_id="20",
        )
        actor = DiscordContext(guild_id="10", channel_id="20", discord_user_id="30")
        bind_actor(db, actor)
        service = PersonaChatService(db, SimpleNamespace(), policy, asyncio.Semaphore(1))
        operation = AsyncMock(side_effect=failure)
        monkeypatch.setattr(service, "_chat_locked", operation)
        with pytest.raises(AppError) as caught:
            await service.chat("synthetic", actor)
        assert caught.value.code == "request_unavailable"
        assert "private-error" not in str(caught.value)
        operation.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command", ["sync_commands", "status_command", "reload_persona", "db_status"]
)
async def test_diagnostic_direct_callback_authorization(command):
    bot = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            release_security_mode=True,
            discord_guild_id="10",
            discord_chat_channel_id="20",
            discord_owner_user_ids="30",
        )
    )
    cog = DiagnosticsCog(bot)
    event = SimpleNamespace(guild_id=10, channel_id=20, user=SimpleNamespace(id=31))
    with pytest.raises(AppError):
        await getattr(cog, command).callback(cog, event)


@pytest.mark.asyncio
async def test_forged_public_voice_source_refused_before_asset_io(monkeypatch):
    resolver = AsyncMock()
    monkeypatch.setattr("bot.views.voice_asset_view._resolve_audio_path", resolver)
    bot = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            release_security_mode=True,
            voice_asset_feature_enabled=True,
            discord_guild_id="10",
            discord_chat_channel_id="20",
        ),
        user=SimpleNamespace(id=99),
        voice_cooldowns=SimpleNamespace(acquire=AsyncMock()),
    )
    event = SimpleNamespace(
        client=bot,
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30),
        message=SimpleNamespace(author=SimpleNamespace(id=98)),
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    await PublicVoiceAssetButton(uuid4()).callback(event)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_modal_actor_is_not_a_valid_admin_session():
    from bot.checks.security import admin_session

    bot = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            release_security_mode=True,
            discord_guild_id="10",
            discord_chat_channel_id="20",
            discord_owner_user_ids="30",
        )
    )
    event = SimpleNamespace(
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30),
        created_at=datetime.now(UTC) - timedelta(minutes=16),
    )
    with pytest.raises(AppError, match="expired"):
        async with admin_session(bot, event):
            pytest.fail("expired event admitted")


@pytest.mark.asyncio
async def test_native_compatibility_fallback_rejects_malformed_message(monkeypatch):
    clients = []

    def make_client(**kwargs):
        first = not clients

        def handle(request):
            return httpx.Response(500) if first else httpx.Response(200, json={"message": []})

        client = original_client(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    policy = Settings(
        _env_file=None,
        release_security_mode=True,
        llm_api_key="synthetic",
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_max_tokens=128,
    )
    request = SimpleNamespace(
        system_prompt="style", corrective_instruction=None, history=[], user_message="demo"
    )
    with pytest.raises(AppError, match="provider failed"):
        await OpenAICompatibleLLMClient(policy).generate(request)
    assert len(clients) == 2


@pytest.mark.asyncio
async def test_ingress_bound_includes_cooldown_replies_and_duplicates():
    from bot.checks.cooldown import CooldownExceeded
    from bot.cogs.chat import ChatCog

    released = asyncio.Event()

    async def blocked_send(*args, **kwargs):
        await released.wait()

    send = AsyncMock(side_effect=blocked_send)
    bot = SimpleNamespace(
        user=SimpleNamespace(id=99),
        settings=Settings(
            _env_file=None,
            discord_guild_id="10",
            discord_chat_channel_id="20",
            max_pending_chat_requests=2,
        ),
        cooldowns=SimpleNamespace(acquire=AsyncMock(side_effect=CooldownExceeded(1))),
    )
    cog = ChatCog(bot)

    def message(index):
        return SimpleNamespace(
            id=index,
            content="synthetic",
            author=SimpleNamespace(id=30 + index, bot=False),
            webhook_id=None,
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20, send=send),
            mentions=[],
        )

    first, second = message(1), message(2)
    tasks = [
        asyncio.create_task(cog.on_message(first)),
        asyncio.create_task(cog.on_message(second)),
    ]
    try:
        await asyncio.sleep(0)
        await cog.on_message(message(3))
        await cog.on_message(first)
        assert send.await_count == 2
        assert cog.admission.pending == 2
    finally:
        released.set()
        await asyncio.gather(*tasks)
    assert cog.admission.pending == 0


def test_rejection_notifications_have_a_global_bound():
    from bot.cogs.chat import ChatCog

    cog = ChatCog(SimpleNamespace())
    assert sum(cog._allow_notification() for _ in range(1000)) == 60
    assert len(cog._notifications) == 60
