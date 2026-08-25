import asyncio
import logging
from pathlib import Path
from time import monotonic
from uuid import UUID

import discord
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository

logger = logging.getLogger(__name__)


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
        async with self.db_sessionmaker() as db:
            validation = await UtteranceVoiceAssetRepository(db, self.settings).resolve_audio_path(
                self.utterance_id
            )
        if not validation.valid:
            logger.info(
                "Voice asset unavailable utterance_id=%s reason=%s",
                self.utterance_id,
                validation.reason,
            )
            return None
        return validation.path

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
