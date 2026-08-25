import discord
from discord import app_commands

from app.core.config import Settings


def is_owner(settings: Settings, user_id: int | str) -> bool:
    return str(user_id) in settings.discord_owner_user_ids


def interaction_is_guild_admin(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        settings = interaction.client.settings
        if not is_owner(settings, interaction.user.id):
            raise app_commands.CheckFailure("Bot owner permission required.")
        return True

    return app_commands.check(predicate)


def owner_or_guild_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        settings = interaction.client.settings
        if not (is_owner(settings, interaction.user.id) or interaction_is_guild_admin(interaction)):
            raise app_commands.CheckFailure("Bot owner or Manage Server permission required.")
        return True

    return app_commands.check(predicate)
