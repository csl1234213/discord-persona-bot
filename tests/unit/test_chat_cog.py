import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.schemas.chat import DiscordContext
from bot.adapters.message_adapter import REDIRECT_MESSAGE
from bot.checks.cooldown import CooldownManager
from bot.cogs.chat import ChatCog


class AsyncContext:
    async def __aenter__(self):
        return SimpleNamespace(info={})

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def interaction():
    return SimpleNamespace(
        id=1,
        guild_id=10,
        channel_id=20,
        channel=SimpleNamespace(id=20),
        user=SimpleNamespace(id=30, roles=[]),
        response=SimpleNamespace(send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_reset_settings_and_persona_info_callbacks(monkeypatch):
    service = SimpleNamespace(
        reset=AsyncMock(return_value=1),
        persona_info=AsyncMock(
            return_value=(
                SimpleNamespace(
                    display_name="人物",
                    description="说明",
                    coverage_threshold=0.3,
                    disclosure="披露",
                ),
                SimpleNamespace(version="1.0.0"),
            )
        ),
    )
    bot = SimpleNamespace(db_sessionmaker=lambda: AsyncContext())
    cog = ChatCog(bot)
    monkeypatch.setattr(cog, "_service", lambda db: service)
    current = interaction()
    await cog.chat_reset.callback(cog, current)
    assert "已重置" in current.response.send_message.await_args.args[0]
    await cog.chat_settings.callback(cog, current)
    assert "安全边界" in current.response.send_message.await_args.args[0]
    assert current.response.send_message.await_args.kwargs["view"] is not None
    await cog.persona_info.callback(cog, current)
    assert current.response.send_message.await_args.kwargs["embed"].title == "人物"


@pytest.mark.asyncio
async def test_dm_is_ignored_and_chat_channel_mention_invokes_chat(monkeypatch):
    result = SimpleNamespace(answer="回答")
    service = SimpleNamespace(chat=AsyncMock(return_value=result))
    typing = AsyncContext()
    channel = SimpleNamespace(id=20, typing=lambda: typing, send=AsyncMock())
    message = SimpleNamespace(
        id=50,
        content="<@99> 你好",
        author=SimpleNamespace(id=30, bot=False, roles=[]),
        webhook_id=None,
        guild=SimpleNamespace(id=10),
        channel=channel,
        mentions=[SimpleNamespace(id=99)],
    )
    bot = SimpleNamespace(
        user=SimpleNamespace(id=99),
        settings=Settings(
            discord_guild_id="10",
            discord_chat_channel_id="20",
            discord_sync_commands_on_startup=False,
        ),
        db_sessionmaker=lambda: AsyncContext(),
        cooldowns=SimpleNamespace(acquire=AsyncMock()),
        redirect_cooldowns=SimpleNamespace(acquire=AsyncMock()),
        chat_semaphore=asyncio.Semaphore(8),
    )
    cog = ChatCog(bot)
    monkeypatch.setattr(cog, "_service", lambda db: service)
    renderer = AsyncMock()
    monkeypatch.setattr("bot.cogs.chat.send_message_result", renderer)
    monkeypatch.setattr(
        "bot.cogs.chat.context_from_message",
        lambda value: DiscordContext(guild_id="10", channel_id="20", discord_user_id="30"),
    )
    await cog.on_message(message)
    assert service.chat.await_args.args[0] == "你好"
    renderer.assert_awaited_once_with(message, result, disclose=False)

    dm = SimpleNamespace(
        author=SimpleNamespace(id=30, bot=False, roles=[]),
        webhook_id=None,
        guild=None,
        channel=channel,
        mentions=[],
        content="你好",
    )
    await cog.on_message(dm)
    assert service.chat.await_count == 1
    assert channel.send.await_count == 0


@pytest.mark.asyncio
async def test_other_channel_mention_only_redirects_without_chat_side_effects(monkeypatch):
    channel = SimpleNamespace(id=21, send=AsyncMock())
    message = SimpleNamespace(
        id=51,
        content="<@99> 你是谁",
        author=SimpleNamespace(id=30, bot=False, roles=[]),
        webhook_id=None,
        guild=SimpleNamespace(id=10),
        channel=channel,
        mentions=[SimpleNamespace(id=99)],
    )
    service = SimpleNamespace(chat=AsyncMock())
    bot = SimpleNamespace(
        user=SimpleNamespace(id=99),
        settings=Settings(discord_guild_id="10", discord_chat_channel_id="20"),
        redirect_cooldowns=SimpleNamespace(acquire=AsyncMock()),
    )
    cog = ChatCog(bot)
    monkeypatch.setattr(cog, "_service", lambda db: service)

    await cog.on_message(message)

    channel.send.assert_awaited_once()
    assert channel.send.await_args.args == (REDIRECT_MESSAGE,)
    assert service.chat.await_count == 0
    bot.redirect_cooldowns.acquire.assert_awaited_once_with("30", "10")


@pytest.mark.asyncio
async def test_many_concurrent_members_in_same_guild_all_receive_answers(monkeypatch):
    result = SimpleNamespace(answer="回答")
    active = 0
    maximum = 0

    async def concurrent_chat(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return result

    service = SimpleNamespace(chat=AsyncMock(side_effect=concurrent_chat))
    typing = AsyncContext()

    def guild_message(message_id: int, user_id: int):
        return SimpleNamespace(
            id=message_id,
            content="<@99> 你好",
            author=SimpleNamespace(id=user_id, bot=False, roles=[]),
            webhook_id=None,
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20, typing=lambda: typing, send=AsyncMock()),
            mentions=[SimpleNamespace(id=99)],
        )

    bot = SimpleNamespace(
        user=SimpleNamespace(id=99),
        settings=Settings(
            discord_guild_id="10",
            discord_chat_channel_id="20",
            discord_sync_commands_on_startup=False,
        ),
        db_sessionmaker=lambda: AsyncContext(),
        cooldowns=CooldownManager(user_seconds=3, guild_seconds=1),
        redirect_cooldowns=CooldownManager(user_seconds=10, guild_seconds=0),
        chat_semaphore=asyncio.Semaphore(4),
    )
    cog = ChatCog(bot)
    monkeypatch.setattr(cog, "_service", lambda db: service)
    renderer = AsyncMock()
    monkeypatch.setattr("bot.cogs.chat.send_message_result", renderer)
    monkeypatch.setattr(
        "bot.cogs.chat.context_from_message",
        lambda value: DiscordContext(
            guild_id="10",
            channel_id="20",
            discord_user_id=str(value.author.id),
        ),
    )

    messages = [guild_message(1000 + index, 2000 + index) for index in range(25)]
    await asyncio.gather(*(cog.on_message(message) for message in messages))

    assert service.chat.await_count == len(messages)
    assert renderer.await_count == len(messages)
    assert maximum == 4
    for message in messages:
        renderer.assert_any_await(message, result, disclose=False)
