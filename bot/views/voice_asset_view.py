import asyncio
import logging
from pathlib import Path
from time import monotonic
from uuid import UUID

import discord
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository
from bot.checks.cooldown import CooldownExceeded
from bot.checks.security import interaction_in_scope

logger = logging.getLogger(__name__)


async def _resolve_audio_path(
    db_sessionmaker: async_sessionmaker, settings: Settings, utterance_id: UUID
) -> Path | None:
    async with db_sessionmaker() as db:
        validation = await UtteranceVoiceAssetRepository(db, settings).resolve_audio_path(
            utterance_id
        )
    if not validation.valid:
        logger.info(
            "Voice asset unavailable utterance_id=%s reason=%s",
            utterance_id,
            validation.reason,
        )
        return None
    return validation.path


class PublicVoiceAssetButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"voice:v1:(?P<utterance_id>[0-9a-fA-F-]{36})",
):
    """Public voice playback control whose asset ID survives bot restarts."""

    # Serialize clicks on the same source message, including interactions that
    # were already in flight when the first click removed the button.
    _message_locks = tuple(asyncio.Lock() for _ in range(64))

    def __init__(self, utterance_id: UUID) -> None:
        self.utterance_id = utterance_id
        button = discord.ui.Button(
            label="聼語音",
            emoji="🔊",
            style=discord.ButtonStyle.secondary,
            custom_id=f"voice:v1:{utterance_id}",
        )
        super().__init__(button)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item, match
    ) -> "PublicVoiceAssetButton":
        return cls(UUID(match["utterance_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        settings = bot.settings
        if not settings.voice_asset_feature_enabled:
            await interaction.response.send_message(
                "语音功能当前未启用。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if (
            interaction.guild_id is None
            or settings.discord_guild_id is None
            or str(interaction.guild_id) != settings.discord_guild_id
            or settings.discord_chat_channel_id is None
            or str(interaction.channel_id) != settings.discord_chat_channel_id
        ):
            await interaction.response.send_message(
                "此语音只能在机器人指定的服务器频道播放。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            await bot.voice_cooldowns.acquire(str(interaction.user.id), str(interaction.guild_id))
        except CooldownExceeded:
            await interaction.response.send_message(
                "操作过快，请稍后再试。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if getattr(settings, "release_security_mode", False):
                source = interaction.message
                if (
                    source is None
                    or not getattr(bot, "user", None)
                    or source.author.id != bot.user.id
                ):
                    await interaction.edit_original_response("Invalid voice source.")
                    return
                controls = [child.custom_id for row in source.components for child in row.children]
                if self.item.custom_id not in controls:
                    await interaction.edit_original_response("Expired voice source.")
                    return
            audio_path = await _resolve_audio_path(bot.db_sessionmaker, settings, self.utterance_id)
            if audio_path is None:
                await interaction.edit_original_response(
                    "本地语音文件当前不可用，请稍后再试。",
                )
                return

            source_message = interaction.message
            if source_message is None:
                await interaction.edit_original_response("找不到原回复，未重复发送语音。")
                return

            if getattr(settings, "release_security_mode", False):
                if not getattr(bot, "user", None) or source_message.author.id != bot.user.id:
                    await interaction.edit_original_response("Invalid voice source.")
                    return
                controls = [
                    child.custom_id for row in source_message.components for child in row.children
                ]
                if self.item.custom_id not in controls:
                    await interaction.edit_original_response("Expired voice source.")
                    return

            lock = self._message_locks[source_message.id % len(self._message_locks)]
            async with lock:
                # Refresh inside the lock: another concurrent click may already
                # have attached the same asset and removed the button.
                source_message = await source_message.channel.fetch_message(source_message.id)
                if any(
                    attachment.filename == audio_path.name
                    for attachment in source_message.attachments
                ):
                    await interaction.edit_original_response(
                        content="语音已附在上方回复中，可直接播放。"
                    )
                    return

                voice_file = discord.File(audio_path, filename=audio_path.name)
                try:
                    await source_message.edit(
                        attachments=[*source_message.attachments, voice_file],
                        view=None,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    voice_file.close()

            await interaction.edit_original_response(content="语音已附在上方回复中，可直接播放。")
        except (OSError, discord.DiscordException):
            logger.exception("Public voice upload failed utterance_id=%s", self.utterance_id)
            await interaction.edit_original_response(content="语音上传失败，请稍后再试。")


class PublicVoiceAssetView(discord.ui.View):
    def __init__(self, utterance_id: UUID) -> None:
        super().__init__(timeout=None)
        self.add_item(PublicVoiceAssetButton(utterance_id))


class VoiceAssetView(discord.ui.View):
    def __init__(
        self,
        *,
        requester_user_id: int,
        utterance_id: UUID,
        db_sessionmaker: async_sessionmaker,
        settings: Settings,
    ) -> None:
        super().__init__(timeout=float(settings.voice_button_timeout_seconds))
        self.requester_user_id = requester_user_id
        self.utterance_id = utterance_id
        self.db_sessionmaker = db_sessionmaker
        self.settings = settings
        self.message: discord.Message | None = None
        self._lock = asyncio.Lock()
        self._last_click_at = 0.0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.settings.release_security_mode and (
            not self.settings.voice_asset_feature_enabled
            or not interaction_in_scope(interaction, self.settings)
            or self.is_finished()
        ):
            return False
        if interaction.user.id == self.requester_user_id:
            return True
        await interaction.response.send_message(
            "只有发起这次对话的用户可以播放该语音。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return False

    @discord.ui.button(label="聼語音", emoji="🔊", style=discord.ButtonStyle.secondary)
    async def play_voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.interaction_check(interaction):
            return
        now = monotonic()
        if now - self._last_click_at < 2.0:
            await interaction.response.send_message(
                "操作过快，请稍后再试。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        self._last_click_at = now
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self._lock.locked():
            await interaction.followup.send(
                "语音正在上传，请稍候。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        async with self._lock:
            button.disabled = True
            await self._edit_view_safely()
            try:
                audio_path = await self._resolve_audio_path()
                if audio_path is None:
                    await interaction.followup.send(
                        "该语音当前不可用，请稍后再试。",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                voice_file = discord.File(audio_path, filename=audio_path.name)
                try:
                    await interaction.followup.send(
                        file=voice_file,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    voice_file.close()
            except (OSError, discord.DiscordException):
                logger.exception(
                    "Voice attachment upload failed utterance_id=%s", self.utterance_id
                )
                await interaction.followup.send(
                    "语音上传失败，请稍后再试。",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                button.disabled = False
                await self._edit_view_safely()

    async def _resolve_audio_path(self) -> Path | None:
        return await _resolve_audio_path(self.db_sessionmaker, self.settings, self.utterance_id)

    async def _edit_view_safely(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.DiscordException:
            logger.info("Unable to refresh voice button state")

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self._edit_view_safely()
