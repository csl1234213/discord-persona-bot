import re

import discord

from app.core.config import Settings
from app.schemas.chat import DiscordContext


def should_process_message(
    message: discord.Message, *, bot_user_id: int, settings: Settings
) -> bool:
    if message.author.bot or message.webhook_id is not None:
        return False
    if message.guild is None:
        return True
    if (
        settings.discord_allowed_guild_ids
        and str(message.guild.id) not in settings.discord_allowed_guild_ids
    ):
        return False
    if any(user.id == bot_user_id for user in message.mentions):
        return True
    return bool(
        settings.discord_enable_message_content_intent
        and str(message.channel.id) in settings.discord_auto_reply_channel_ids
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
