import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import text

from bot.checks.admin import owner_only
from bot.checks.security import admin_session
from bot.cogs.chat import DISCLOSURE


class DiagnosticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="显示机器人命令与安全边界")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Discord Persona Bot", color=discord.Color.from_rgb(39, 76, 119)
        )
        embed.description = "使用 `/chat` 对话，使用 `/persona-info` 查看当前人物。"
        embed.set_footer(text=DISCLOSURE)
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="bot-status", description="查看机器人就绪状态")
    @owner_only()
    async def status_command(self, interaction: discord.Interaction) -> None:
        if self.bot.settings.release_security_mode:
            async with admin_session(self.bot, interaction):
                pass
        intents = self.bot.intents
        await interaction.response.send_message(
            (
                "ready=true\n"
                f"message_content={intents.message_content}\n"
                f"guild_members={intents.members}\n"
                f"guild_presences={intents.presences}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="bot-sync-commands", description="同步命令到配置的服务器")
    @owner_only()
    async def sync_commands(self, interaction: discord.Interaction) -> None:
        if self.bot.settings.release_security_mode:
            async with admin_session(self.bot, interaction):
                pass
        await interaction.response.defer(ephemeral=True)
        guild_id = self.bot.settings.discord_guild_id
        if guild_id is None:
            await interaction.followup.send("未配置 DISCORD_GUILD_ID。", ephemeral=True)
            return
        guild = discord.Object(id=int(guild_id))
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await interaction.followup.send(f"已同步 {len(synced)} 个命令。", ephemeral=True)

    @app_commands.command(name="bot-reload-persona", description="重新读取当前人物配置")
    @owner_only()
    async def reload_persona(self, interaction: discord.Interaction) -> None:
        if self.bot.settings.release_security_mode:
            async with admin_session(self.bot, interaction):
                pass
        await interaction.response.send_message(
            "人物配置由数据库按请求读取，无需清空进程缓存。", ephemeral=True
        )

    @app_commands.command(name="bot-db-status", description="检查数据库连接与迁移就绪状态")
    @owner_only()
    async def db_status(self, interaction: discord.Interaction) -> None:
        if self.bot.settings.release_security_mode:
            async with admin_session(self.bot, interaction):
                pass
        async with self.bot.db_sessionmaker() as db:
            await db.execute(text("SELECT 1"))
        await interaction.response.send_message("database=ready", ephemeral=True)
