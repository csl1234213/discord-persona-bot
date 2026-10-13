import asyncio
import logging
from contextlib import suppress

import discord
from discord import app_commands
from discord.ext import commands

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import build_engine, build_sessionmaker, init_database
from app.llm.factory import build_llm_client
from app.services.conversation_lock_manager import ConversationLockManager
from app.services.privacy_service import PrivacyService
from bot.checks.cooldown import CooldownManager
from bot.checks.security import interaction_in_scope
from bot.cogs.chat import ChatCog
from bot.cogs.diagnostics import DiagnosticsCog
from bot.cogs.persona_admin import PersonaAdminCog
from bot.cogs.style_admin import StyleAdminCog
from bot.cogs.utterance_admin import UtteranceAdminCog
from bot.views.voice_asset_view import PublicVoiceAssetButton

logger = logging.getLogger(__name__)


class ConfiguredGuildCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        settings = interaction.client.settings
        return interaction_in_scope(interaction, settings)


def build_intents(settings: Settings) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = settings.discord_enable_message_content_intent
    return intents


class PersonaDiscordBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            tree_cls=ConfiguredGuildCommandTree,
            intents=build_intents(settings),
            application_id=settings.application_id_int,
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=None,
        )
        self.settings = settings
        self.settings.ensure_local_directories()
        self.db_engine = build_engine(
            settings.database_url,
            max_bytes=settings.security_database_max_bytes
            if settings.release_security_mode
            else None,
        )
        self.cleanup_task = None
        self.db_sessionmaker = build_sessionmaker(self.db_engine)
        self.llm_client = build_llm_client(settings)
        self.chat_semaphore = asyncio.Semaphore(settings.max_concurrent_chat_requests)
        self.llm_semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
        self.conversation_locks = ConversationLockManager()
        self.cooldowns = CooldownManager(
            settings.user_cooldown_seconds, settings.guild_cooldown_seconds
        )
        self.voice_cooldowns = CooldownManager(2.0, 0.0)
        self.redirect_cooldowns = CooldownManager(settings.mention_redirect_cooldown_seconds, 0)
        self.ready_for_discord = False

    async def setup_hook(self) -> None:
        self.tree.on_error = self.on_app_command_error
        await init_database(self.db_engine)
        if self.settings.release_security_mode:
            async with self.db_sessionmaker() as db:
                grant_local_admin(db)
                await PrivacyService(db, self.settings).cleanup()
            self.cleanup_task = asyncio.create_task(self._retention_loop())
        self.add_dynamic_items(PublicVoiceAssetButton)
        await self.add_cog(ChatCog(self))
        await self.add_cog(DiagnosticsCog(self))
        await self.add_cog(PersonaAdminCog(self))
        await self.add_cog(StyleAdminCog(self))
        await self.add_cog(UtteranceAdminCog(self))
        if self.settings.discord_sync_commands_on_startup:
            await self.sync_application_commands()
        self.ready_for_discord = True

    async def on_ready(self) -> None:
        guild_id = self.settings.discord_guild_id
        channel_id = self.settings.discord_chat_channel_id
        if guild_id is not None and channel_id is not None:
            guild = self.get_guild(int(guild_id))
            channel = guild.get_channel(int(channel_id)) if guild else None
            if guild is None or not isinstance(channel, discord.TextChannel):
                logger.critical(
                    "Configured Discord guild/channel validation failed "
                    "guild_valid=%s channel_valid=%s",
                    guild is not None,
                    isinstance(channel, discord.TextChannel),
                )
                await self.close()
                return
            logger.info("Discord guild validated guild_id=%s", guild_id)
            logger.info("Discord chat channel validated channel_id=%s", channel_id)
            try:
                async with self.db_sessionmaker() as db:
                    persona, version = await ChatCog(self)._service(db).persona_info(guild_id)
            except Exception:
                logger.exception("Active persona validation failed")
                await self.close()
                return
            logger.info(
                "Persona loaded persona_id=%s persona_version=%s",
                persona.id,
                version.version,
            )
        logger.info("Discord connected discord_user_id=%s", self.user.id if self.user else "")
        logger.info(
            "Bot ready discord_ready=true discord_user_id=%s",
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
            logger.error(
                "Unhandled app command error interaction_id=%s error_type=%s",
                interaction.id,
                type(original).__name__,
            )
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
        if self.cleanup_task:
            self.cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.cleanup_task
        await super().close()
        await self.db_engine.dispose()

    async def _retention_loop(self):
        while True:
            await asyncio.sleep(self.settings.security_cleanup_interval_seconds)
            try:
                async with self.db_sessionmaker() as db:
                    grant_local_admin(db)
                    await PrivacyService(db, self.settings).cleanup()
            except Exception:
                logger.error(
                    "Retention maintenance failed; bounded storage admission remains active"
                )

    async def sync_application_commands(self) -> list[discord.app_commands.AppCommand]:
        guild_id = self.settings.discord_guild_id or self.settings.discord_test_guild_id
        if guild_id is None:
            raise RuntimeError("DISCORD_GUILD_ID is required to sync guild-scoped commands.")
        guild = discord.Object(id=int(guild_id))
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %s commands to configured guild", len(synced))
        return synced


def create_bot(settings: Settings | None = None) -> PersonaDiscordBot:
    return PersonaDiscordBot(settings or Settings())
