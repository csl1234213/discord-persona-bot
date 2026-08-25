import discord
from discord import app_commands


def allowed_guild_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        allowed = interaction.client.settings.discord_allowed_guild_ids
        if (
            interaction.guild_id is not None
            and allowed
            and str(interaction.guild_id) not in allowed
        ):
            raise app_commands.CheckFailure("This guild is not allow-listed.")
        return True

    return app_commands.check(predicate)
