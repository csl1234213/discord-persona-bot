from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from bot import main as main_module
from bot.client import build_intents, create_bot

TEST_DATABASE_URL = "sqlite+aiosqlite:///./.cache/tests/foundation.sqlite3"


def test_default_intents_are_minimal():
    intents = build_intents(Settings(discord_sync_commands_on_startup=False))
    assert intents.guilds
    assert intents.guild_messages
    assert intents.dm_messages
    assert not intents.message_content
    assert not intents.members
    assert not intents.presences


def test_message_content_intent_is_explicit():
    settings = Settings(
        discord_enable_message_content_intent=True,
        discord_sync_commands_on_startup=False,
    )
    assert build_intents(settings).message_content


def test_empty_and_comma_separated_snowflake_environment_values(monkeypatch):
    monkeypatch.setenv("DISCORD_OWNER_USER_IDS", "10,20")
    monkeypatch.setenv("DISCORD_ALLOWED_GUILD_IDS", "30")
    monkeypatch.setenv("DISCORD_AUTO_REPLY_CHANNEL_IDS", "")
    settings = Settings(_env_file=None)
    assert settings.discord_owner_user_ids == {"10", "20"}
    assert settings.discord_allowed_guild_ids == {"30"}
    assert settings.discord_auto_reply_channel_ids == set()


@pytest.mark.asyncio
async def test_bot_registers_phase_one_commands_without_network():
    bot = create_bot(
        Settings(
            discord_sync_commands_on_startup=False,
            llm_provider="fake",
            database_url=TEST_DATABASE_URL,
        )
    )
    await bot.setup_hook()
    names = {command.name for command in bot.tree.get_commands()}
    assert {
        "chat",
        "chat-reset",
        "chat-settings",
        "persona-info",
        "help",
        "bot-status",
        "persona",
        "utterance",
        "style",
        "personality",
        "address",
    } <= names
    assert bot.ready_for_discord
    await bot.close()


@pytest.mark.asyncio
async def test_test_guild_sync_uses_guild_scope():
    bot = create_bot(
        Settings(
            discord_test_guild_id="123456789",
            discord_sync_commands_on_startup=False,
            database_url=TEST_DATABASE_URL,
        )
    )
    bot.tree.copy_global_to = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])
    await bot.sync_application_commands()
    bot.tree.copy_global_to.assert_called_once()
    bot.tree.sync.assert_awaited_once()
    assert bot.tree.sync.await_args.kwargs["guild"].id == 123456789
    await bot.close()


@pytest.mark.asyncio
async def test_global_sync():
    bot = create_bot(
        Settings(discord_sync_commands_on_startup=False, database_url=TEST_DATABASE_URL)
    )
    bot.tree.sync = AsyncMock(return_value=[])
    assert await bot.sync_application_commands() == []
    await bot.close()


@pytest.mark.asyncio
async def test_chat_defers_before_model_execution(monkeypatch):
    bot = create_bot(
        Settings(discord_sync_commands_on_startup=False, database_url=TEST_DATABASE_URL)
    )
    await bot.setup_hook()
    cog = bot.get_cog("ChatCog")
    events: list[str] = []
    service = SimpleNamespace(
        chat=AsyncMock(side_effect=lambda *args: events.append("service") or SimpleNamespace())
    )
    monkeypatch.setattr(cog, "_service", lambda db: service)
    monkeypatch.setattr(
        "bot.cogs.chat.context_from_interaction",
        lambda interaction: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "bot.cogs.chat.send_interaction_result",
        AsyncMock(side_effect=lambda *args, **kwargs: events.append("send")),
    )
    interaction = SimpleNamespace(
        id=123,
        user=SimpleNamespace(id=10),
        guild_id=None,
        response=SimpleNamespace(
            defer=AsyncMock(side_effect=lambda **kwargs: events.append("defer"))
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.chat.callback(cog, interaction, "测试", False, True)
    assert events == ["defer", "service", "send"]
    await bot.close()


@pytest.mark.asyncio
async def test_diagnostic_callbacks_are_safe():
    bot = create_bot(
        Settings(discord_sync_commands_on_startup=False, database_url=TEST_DATABASE_URL)
    )
    await bot.setup_hook()
    cog = bot.get_cog("DiagnosticsCog")
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    await cog.help_command.callback(cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "AI 语言风格模拟" in embed.footer.text
    await cog.status_command.callback(cog, interaction)
    assert "message_content=False" in interaction.response.send_message.await_args.args[0]
    await bot.close()


def test_main_requires_token(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(discord_sync_commands_on_startup=False),
    )
    with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
        main_module.main()


def test_main_runs_bot_without_exposing_token(monkeypatch):
    settings = Settings(
        discord_bot_token="test-only-token",
        discord_sync_commands_on_startup=False,
        database_url="sqlite+aiosqlite:///./.cache/tests/main.sqlite3",
    )
    fake_bot = SimpleNamespace(run=MagicMock())
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "create_bot", lambda value: fake_bot)
    main_module.main()
    fake_bot.run.assert_called_once_with("test-only-token", log_handler=None)
