import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import build_engine, build_sessionmaker, init_database
from app.llm.fake import FakeLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from bot.checks.cooldown import CooldownManager
from bot.cogs.chat import ChatCog
from bot.cogs.diagnostics import DiagnosticsCog
from bot.cogs.persona_admin import PersonaAdminCog
from bot.cogs.style_admin import StyleAdminCog
from bot.cogs.utterance_admin import UtteranceAdminCog

logger = logging.getLogger(__name__)


def build_intents(settings: Settings) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.dm_messages = True
    intents.guild_messages = True
    intents.message_content = settings.discord_enable_message_content_intent
    return intents


class PersonaDiscordBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(settings),
            application_id=settings.application_id_int,
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=None,
        )
        self.settings = settings
        self.settings.ensure_local_directories()
        self.db_engine = build_engine(settings.database_url)
        self.db_sessionmaker = build_sessionmaker(self.db_engine)
        self.llm_client = (
            OpenAICompatibleLLMClient(settings)
            if settings.llm_provider == "openai_compatible"
            else FakeLLMClient()
        )
        self.chat_semaphore = asyncio.Semaphore(settings.max_concurrent_chat_requests)
        self.llm_semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
        self.cooldowns = CooldownManager(
            settings.user_cooldown_seconds, settings.guild_cooldown_seconds
        )
        self.ready_for_discord = False

    async def setup_hook(self) -> None:
        self.tree.on_error = self.on_app_command_error
        await init_database(self.db_engine)
        await self.add_cog(ChatCog(self))
        await self.add_cog(DiagnosticsCog(self))
        await self.add_cog(PersonaAdminCog(self))
        await self.add_cog(StyleAdminCog(self))
        await self.add_cog(UtteranceAdminCog(self))
        if self.settings.discord_sync_commands_on_startup:
            await self.sync_application_commands()
        self.ready_for_discord = True

    async def on_ready(self) -> None:
        logger.info(
            "Discord gateway ready discord_ready=true discord_user_id=%s",
            self.user.id if self.user else "",
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, AppError):
            message = f"无法完成请求：{original.message}"
        elif isinstance(error, app_commands.CheckFailure):
            message = "你没有执行此命令所需的权限。"
        else:
            logger.exception("Unhandled app command error", exc_info=error)
            message = "命令执行失败，请稍后重试。"
        if interaction.response.is_done():
            await interaction.followup.send(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
        else:
            await interaction.response.send_message(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )

    async def close(self) -> None:
        await super().close()
        await self.db_engine.dispose()

    async def sync_application_commands(self) -> list[discord.app_commands.AppCommand]:
        if self.settings.discord_test_guild_id:
            guild = discord.Object(id=int(self.settings.discord_test_guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s commands to test guild", len(synced))
            return synced
        synced = await self.tree.sync()
        logger.info("Synced %s global commands", len(synced))
        return synced


def create_bot(settings: Settings | None = None) -> PersonaDiscordBot:
    return PersonaDiscordBot(settings or Settings())
