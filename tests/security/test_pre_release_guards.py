"""Negative security regressions; synthetic IDs and bytes only."""

from types import SimpleNamespace

import discord
import pytest

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.services.attachment_import_service import AttachmentImportService
from bot.adapters.message_adapter import MessageRoute, route_message


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["message", "interaction"])
async def test_release_renderer_discloses_and_neutralizes_mentions(transport):
    from unittest.mock import AsyncMock

    from bot.adapters.response_renderer import send_interaction_result, send_message_result

    result = SimpleNamespace(answer="@everyone <@123> synthetic", evidence=[])
    send = AsyncMock()
    if transport == "message":
        request = SimpleNamespace(
            guild=SimpleNamespace(id=10),
            author=SimpleNamespace(id=30),
            channel=SimpleNamespace(send=send),
        )
        await send_message_result(request, result, disclose=True)
    else:
        request = SimpleNamespace(followup=SimpleNamespace(send=send))
        await send_interaction_result(
            request, result, private=False, include_evidence=False, disclose=True
        )
    content = send.await_args.args[0]
    assert "AI 風格模擬" in content
    assert "@everyone" not in content
    assert not send.await_args.kwargs["allowed_mentions"].everyone


@pytest.mark.asyncio
@pytest.mark.parametrize("guild,channel,user", [(11, 20, 1), (10, 21, 1), (10, 20, 2)])
async def test_admin_session_denies_wrong_scope_before_db(guild, channel, user):
    from unittest.mock import MagicMock

    from app.core.errors import AppError
    from bot.checks.security import admin_session

    bot = SimpleNamespace(
        settings=Settings(
            discord_guild_id="10", discord_chat_channel_id="20", discord_owner_user_ids="1"
        ),
        db_sessionmaker=MagicMock(),
    )
    request = SimpleNamespace(guild_id=guild, channel_id=channel, user=SimpleNamespace(id=user))
    with pytest.raises(AppError, match="Administrator"):
        async with admin_session(bot, request):
            pytest.fail("unauthorized session entered")
    bot.db_sessionmaker.assert_not_called()


def message(**changes):
    values = dict(
        author=SimpleNamespace(id=30, bot=False),
        webhook_id=None,
        guild=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=20),
        content="synthetic",
        mentions=[],
        type=discord.MessageType.default,
    )
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"author": SimpleNamespace(id=99, bot=False)},
        {"type": discord.MessageType.pins_add},
        {"content": "   "},
    ],
)
def test_system_self_empty_are_ignored(changes):
    settings = Settings(discord_guild_id="10", discord_chat_channel_id="20")
    assert (
        route_message(message(**changes), bot_user_id=99, settings=settings) == MessageRoute.IGNORE
    )


def test_owner_configuration_is_required():
    settings = Settings(
        discord_guild_id="10",
        discord_chat_channel_id="20",
        discord_enable_message_content_intent=True,
    )
    with pytest.raises(RuntimeError, match="DISCORD_OWNER_USER_IDS"):
        settings.validate_production_discord()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"[]", b"null", b'"record"', b"[" * 1100 + b"]" * 1100],
    ids=["array", "null", "string", "deep"],
)
async def test_invalid_json_shape_is_controlled(db_sessionmaker, payload):
    from uuid import uuid4

    async with db_sessionmaker() as db:
        with pytest.raises(ValidationFailed):
            await AttachmentImportService(db, Settings()).preview(
                version_id=uuid4(),
                filename="test.jsonl",
                content_type=None,
                data=payload,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("create_persona", (None,)),
        ("get_persona", (None,)),
        ("get_version", (None, None)),
        ("require_draft", (None, None)),
        ("create_draft", (None, None)),
        ("add_utterance", (None, None, None)),
        ("set_verified", (None, None, None, True)),
        ("delete_utterance", (None, None, None)),
        ("publish", (None, None)),
        ("activate", (None, None)),
        ("rollback", (None,)),
    ],
)
async def test_direct_admin_service_without_authority_is_denied(method, args):
    from unittest.mock import AsyncMock

    from app.core.errors import AppError
    from app.services.persona_update_service import PersonaUpdateService

    db = AsyncMock()
    db.info = {}
    with pytest.raises(AppError, match="Administrator authority"):
        await getattr(PersonaUpdateService(db), method)(*args)
    db.execute.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, MessageRoute.CHAT),
        ({"guild": None}, MessageRoute.IGNORE),
        ({"guild": SimpleNamespace(id=11)}, MessageRoute.IGNORE),
        ({"webhook_id": 1}, MessageRoute.IGNORE),
        ({"author": SimpleNamespace(id=30, bot=True)}, MessageRoute.IGNORE),
        ({"channel": SimpleNamespace(id=21)}, MessageRoute.IGNORE),
        (
            {"channel": SimpleNamespace(id=21), "mentions": [SimpleNamespace(id=99)]},
            MessageRoute.REDIRECT,
        ),
    ],
)
def test_route_matrix(changes, expected):
    settings = Settings(discord_guild_id="10", discord_chat_channel_id="20")
    assert route_message(message(**changes), bot_user_id=99, settings=settings) == expected


def test_admission_is_bounded_and_deduplicates():
    from app.core.errors import AppError
    from bot.checks.admission import ChatAdmission

    admission = ChatAdmission(2, seen_limit=3)
    with admission.hold("first"), admission.hold("second"):
        with pytest.raises(AppError, match="full"):
            with admission.hold("third"):
                pytest.fail("queue overflow")
        with pytest.raises(AppError, match="Duplicate"):
            with admission.hold("first"):
                pytest.fail("duplicate charged")
    assert admission.pending == 0
    for index in range(100):
        with admission.hold(index):
            pass
    assert len(admission.seen) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_changes",
    [
        {"guild_id": "11"},
        {"guild_id": None},
        {"channel_id": "21"},
        {"thread_id": "20"},
    ],
)
async def test_chat_core_scope_rejects_before_database_access(context_changes):
    import asyncio
    from unittest.mock import AsyncMock

    from app.core.errors import AppError
    from app.schemas.chat import DiscordContext
    from app.services.persona_chat_service import PersonaChatService

    db, llm = AsyncMock(), AsyncMock()
    settings = Settings(
        release_security_mode=True, discord_guild_id="10", discord_chat_channel_id="20"
    )
    context = DiscordContext(guild_id="10", channel_id="20", discord_user_id="30")
    service = PersonaChatService(db, llm, settings, asyncio.Semaphore(1))
    with pytest.raises(AppError, match="scope"):
        await service.chat("synthetic", context.model_copy(update=context_changes))
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    llm.generate.assert_not_called()


def test_file_secret_and_safe_configuration_error(tmp_path, monkeypatch):
    from app.core.config import get_settings

    secret = tmp_path / "synthetic_secret"
    secret.write_text("synthetic-not-a-token", encoding="utf-8")
    settings = Settings(discord_bot_token_file=secret)
    assert settings.discord_bot_token.get_secret_value() == "synthetic-not-a-token"
    assert "synthetic-not-a-token" not in repr(settings)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "synthetic-sensitive-invalid-value")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError) as exc:
        get_settings()
    assert "synthetic-sensitive" not in str(exc.value)
    get_settings.cache_clear()


def test_logging_redacts_credentials_and_urls():
    from app.core.logging import redact

    assert "synthetic-secret" not in redact("Authorization Bearer synthetic-secret")
    assert "private.example" not in redact("POST https://private.example/?token=synthetic-secret")
