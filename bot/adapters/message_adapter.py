import re
from enum import StrEnum

import discord

from app.core.config import Settings
from app.schemas.chat import DiscordContext


class MessageRoute(StrEnum):
    IGNORE = "ignore"
    CHAT = "chat"
    REDIRECT = "redirect"


REDIRECT_MESSAGE = "請到機器人專區🤖與我聊天"


def route_message(
    message: discord.Message, *, bot_user_id: int, settings: Settings
) -> MessageRoute:
    """Classify a message using the configured guild/channel allowlist.

    Threads, DMs, bot/webhook messages, and messages from every other guild are
    ignored. Mentions outside the configured chat channel only receive a static
    redirect and must not enter the Persona chat pipeline.
    """
    if (
        getattr(message.author, "bot", False)
        or message.author.id == bot_user_id
        or message.webhook_id is not None
        or getattr(message, "type", discord.MessageType.default)
        not in {discord.MessageType.default, discord.MessageType.reply}
        or not getattr(message, "content", "").strip()
    ):
        return MessageRoute.IGNORE

    guild = message.guild
    if guild is None or settings.discord_guild_id is None:
        return MessageRoute.IGNORE
    if str(guild.id) != settings.discord_guild_id:
        return MessageRoute.IGNORE

    channel = message.channel
    if isinstance(channel, discord.Thread):
        return MessageRoute.IGNORE

    if (
        settings.discord_chat_channel_id is not None
        and str(channel.id) == settings.discord_chat_channel_id
    ):
        return MessageRoute.CHAT

    if any(user.id == bot_user_id for user in message.mentions):
        return MessageRoute.REDIRECT
    return MessageRoute.IGNORE


def should_process_message(
    message: discord.Message, *, bot_user_id: int, settings: Settings
) -> bool:
    """Compatibility helper for callers that only need the chat/no-chat bit."""
    return (
        route_message(message, bot_user_id=bot_user_id, settings=settings)
        == MessageRoute.CHAT
    )


def content_without_mention(content: str, bot_user_id: int) -> str:
    pattern = re.compile(rf"<@!?{bot_user_id}>")
    return pattern.sub("", content).strip()


def context_from_message(message: discord.Message) -> DiscordContext:
    role_ids = [str(role.id) for role in getattr(message.author, "roles", [])]
    channel = message.channel
    return DiscordContext(
        guild_id=str(message.guild.id) if message.guild else None,
        channel_id=str(channel.id),
        thread_id=str(channel.id) if isinstance(channel, discord.Thread) else None,
        discord_user_id=str(message.author.id),
        discord_message_id=str(message.id),
        role_ids=role_ids,
    )
