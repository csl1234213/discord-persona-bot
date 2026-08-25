import discord

from app.schemas.chat import DiscordContext


def context_from_interaction(interaction: discord.Interaction) -> DiscordContext:
    channel = interaction.channel
    role_ids = [str(role.id) for role in getattr(interaction.user, "roles", [])]
    return DiscordContext(
        guild_id=str(interaction.guild_id) if interaction.guild_id else None,
        channel_id=str(interaction.channel_id),
        thread_id=str(channel.id) if isinstance(channel, discord.Thread) else None,
        discord_user_id=str(interaction.user.id),
        discord_message_id=str(interaction.id),
        role_ids=role_ids,
    )
