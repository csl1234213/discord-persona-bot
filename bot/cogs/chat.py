import logging
from collections import deque
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization import bind_actor
from app.core.errors import AppError
from app.core.logging import correlation_id
from app.services.persona_chat_service import PersonaChatService
from app.services.privacy_service import PrivacyService
from bot.adapters.interaction_adapter import context_from_interaction
from bot.adapters.message_adapter import (
    REDIRECT_MESSAGE,
    MessageRoute,
    content_without_mention,
    context_from_message,
    route_message,
)
from bot.adapters.response_renderer import send_interaction_result, send_message_result
from bot.adapters.voice_asset_adapter import build_voice_asset_view
from bot.checks.admission import ChatAdmission
from bot.checks.cooldown import CooldownExceeded
from bot.checks.security import require_origin
from bot.views.personality_view import PersonalityView

logger = logging.getLogger(__name__)
DISCLOSURE = "这是基于所提供材料生成的 AI 语言风格模拟，并非本人，也不代表本人当前观点。"


async def in_configured_chat_channel(interaction: discord.Interaction) -> bool:
    settings = interaction.client.settings
    return (
        settings.discord_guild_id is not None
        and settings.discord_chat_channel_id is not None
        and str(interaction.guild_id) == settings.discord_guild_id
        and str(interaction.channel_id) == settings.discord_chat_channel_id
    )


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._notifications = deque()
        self.admission = ChatAdmission(
            getattr(getattr(bot, "settings", None), "max_pending_chat_requests", 32)
        )

    def _allow_notification(self):
        now = monotonic()
        while self._notifications and now - self._notifications[0] >= 60:
            self._notifications.popleft()
        if len(self._notifications) >= 60:
            return False
        self._notifications.append(now)
        return True

    def _service(self, db) -> PersonaChatService:
        return PersonaChatService(
            db,
            self.bot.llm_client,
            self.bot.settings,
            self.bot.llm_semaphore,
            self.bot.conversation_locks,
        )

    @app_commands.command(name="chat", description="使用当前人物进行严格证据对话")
    @app_commands.check(in_configured_chat_channel)
    @app_commands.describe(
        message="要发送的内容", private="是否仅自己可见", evidence="是否显示证据"
    )
    async def chat(
        self,
        interaction: discord.Interaction,
        message: str,
        private: bool = False,
        evidence: bool = False,
    ) -> None:
        try:
            with self.admission.hold(interaction.id):
                await self._chat_accepted(interaction, message, private, evidence)
        except AppError as exc:
            if exc.code not in {"duplicate_event", "queue_full"}:
                raise

    async def _chat_accepted(self, interaction, message, private, evidence):
        await interaction.response.defer(ephemeral=private, thinking=True)
        token = correlation_id.set(str(interaction.id))
        try:
            await self.bot.cooldowns.acquire(
                str(interaction.user.id),
                str(interaction.guild_id) if interaction.guild_id else None,
            )
            async with self.bot.chat_semaphore:
                async with self.bot.db_sessionmaker() as db:
                    context = context_from_interaction(interaction)
                    bind_actor(db, context)
                    result = await self._service(db).chat(message, context)
            view = await build_voice_asset_view(
                db_sessionmaker=self.bot.db_sessionmaker,
                settings=self.bot.settings,
                result=result,
                requester_user_id=interaction.user.id,
                public=not private,
            )
            if view is None:
                await send_interaction_result(
                    interaction,
                    result,
                    private=private,
                    include_evidence=evidence,
                    disclose=getattr(self.bot.settings, "release_security_mode", False),
                )
            else:
                await send_interaction_result(
                    interaction,
                    result,
                    disclose=getattr(self.bot.settings, "release_security_mode", False),
                    private=private,
                    include_evidence=evidence,
                    view=view,
                )
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.warning("Request failed with controlled storage/provider error")
            return
        except CooldownExceeded as exc:
            if not self._allow_notification():
                return
            await interaction.followup.send(
                f"请求过快，请在 {exc.retry_after:.1f} 秒后重试。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except AppError as exc:
            if exc.code in {"duplicate_event", "queue_full"} or not self._allow_notification():
                return
            await interaction.followup.send(
                f"无法完成请求：{exc.message}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            correlation_id.reset(token)

    @app_commands.command(name="chat-reset", description="仅重置你自己的当前会话")
    @app_commands.check(in_configured_chat_channel)
    async def chat_reset(self, interaction: discord.Interaction) -> None:
        async with self.bot.db_sessionmaker() as db:
            context = context_from_interaction(interaction)
            bind_actor(db, context)
            count = await self._service(db).reset(context)
        await interaction.response.send_message(
            "已重置你的当前会话。" if count else "当前没有可重置的会话。", ephemeral=True
        )

    @app_commands.command(name="chat-erase", description="刪除你在此伺服器及頻道的全部會話資料")
    @app_commands.check(in_configured_chat_channel)
    async def chat_erase(self, interaction: discord.Interaction) -> None:
        context = context_from_interaction(interaction)
        key = (context.guild_id, context.channel_id, context.discord_user_id)
        async with self.bot.conversation_locks.hold(key):
            async with self.bot.db_sessionmaker() as db:
                bind_actor(db, context)
                count = await PrivacyService(db, self.bot.settings).erase(context)
        await interaction.response.send_message(
            f"已刪除你的 {count} 個會話及其訊息。Discord 原訊息、備份及短期安全額度不在此範圍。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="chat-settings", description="查看当前可调整的对话设置")
    @app_commands.check(in_configured_chat_channel)
    async def chat_settings(self, interaction: discord.Interaction) -> None:
        context = context_from_interaction(interaction)

        async def save(save_interaction: discord.Interaction, values: dict[str, float]) -> None:
            if (
                save_interaction.user.id != interaction.user.id
                or save_interaction.guild_id != interaction.guild_id
                or save_interaction.channel_id != interaction.channel_id
            ):
                raise AppError("forbidden", "Conversation settings scope mismatch.")
            require_origin(interaction, save_interaction)
            async with self.bot.db_sessionmaker() as db:
                bind_actor(db, context)
                await self._service(db).update_personality(context, values)
            await save_interaction.response.send_message(
                "会话人格设置已更新；证据边界不受影响。", ephemeral=True
            )

        view = PersonalityView(owner_user_id=interaction.user.id, on_change=save)
        await interaction.response.send_message(
            "先选择人格维度，再选择 0–10 等级。安全边界不会改变。",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="persona-info", description="查看当前人物、版本与披露")
    @app_commands.check(in_configured_chat_channel)
    async def persona_info(self, interaction: discord.Interaction) -> None:
        context = context_from_interaction(interaction)
        async with self.bot.db_sessionmaker() as db:
            persona, version = await self._service(db).persona_info(context.guild_id)
        embed = discord.Embed(title=persona.display_name, description=persona.description)
        embed.add_field(name="活动版本", value=version.version)
        embed.add_field(name="证据阈值", value=f"{persona.coverage_threshold:.2f}")
        embed.set_footer(text=persona.disclosure)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            self.bot.user is None
            or route_message(message, bot_user_id=self.bot.user.id, settings=self.bot.settings)
            == MessageRoute.IGNORE
        ):
            return
        try:
            with self.admission.hold(message.id):
                await self._on_message_accepted(message)
        except AppError as exc:
            if exc.code not in {"duplicate_event", "queue_full"}:
                raise

    async def _on_message_accepted(self, message):
        if self.bot.user is None:
            return
        route = route_message(message, bot_user_id=self.bot.user.id, settings=self.bot.settings)
        if route == MessageRoute.IGNORE:
            return
        if route == MessageRoute.REDIRECT:
            if not self._allow_notification():
                return
            try:
                await self.bot.redirect_cooldowns.acquire(
                    str(message.author.id), str(message.guild.id)
                )
            except CooldownExceeded:
                return
            await message.channel.send(
                REDIRECT_MESSAGE,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        content = content_without_mention(message.content, self.bot.user.id)
        if not content:
            return
        token = correlation_id.set(str(message.id))
        try:
            await self.bot.cooldowns.acquire(
                str(message.author.id), str(message.guild.id) if message.guild else None
            )
            async with message.channel.typing():
                async with self.bot.chat_semaphore:
                    async with self.bot.db_sessionmaker() as db:
                        context = context_from_message(message)
                        bind_actor(db, context)
                        result = await self._service(db).chat(content, context)
            view = await build_voice_asset_view(
                db_sessionmaker=self.bot.db_sessionmaker,
                settings=self.bot.settings,
                result=result,
                requester_user_id=message.author.id,
                public=True,
            )
            if view is None:
                await send_message_result(
                    message,
                    result,
                    disclose=getattr(self.bot.settings, "release_security_mode", False),
                )
            else:
                await send_message_result(
                    message,
                    result,
                    view=view,
                    disclose=getattr(self.bot.settings, "release_security_mode", False),
                )
        except CooldownExceeded as exc:
            if not self._allow_notification():
                return
            await message.channel.send(
                f"请求过快，请在 {exc.retry_after:.1f} 秒后重试。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except AppError as exc:
            if exc.code in {"duplicate_event", "queue_full"} or not self._allow_notification():
                return
            logger.info("Discord message request rejected code=%s", exc.code)
            await message.channel.send(
                f"无法完成请求：{exc.message}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            correlation_id.reset(token)
