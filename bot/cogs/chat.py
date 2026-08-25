import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.core.errors import AppError
from app.core.logging import correlation_id
from app.services.persona_chat_service import PersonaChatService
from bot.adapters.interaction_adapter import context_from_interaction
from bot.adapters.message_adapter import (
    content_without_mention,
    context_from_message,
    should_process_message,
)
from bot.adapters.response_renderer import send_interaction_result, send_message_result
from bot.adapters.voice_asset_adapter import build_voice_asset_view
from bot.checks.cooldown import CooldownExceeded
from bot.views.personality_view import PersonalityView

logger = logging.getLogger(__name__)
DISCLOSURE = "这是基于所提供材料生成的 AI 语言风格模拟，并非本人，也不代表本人当前观点。"


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _service(self, db) -> PersonaChatService:
        return PersonaChatService(
            db,
            self.bot.llm_client,
            self.bot.settings,
            self.bot.llm_semaphore,
        )

    @app_commands.command(name="chat", description="使用当前人物进行严格证据对话")
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
        await interaction.response.defer(ephemeral=private, thinking=True)
        token = correlation_id.set(str(interaction.id))
        try:
            await self.bot.cooldowns.acquire(
                str(interaction.user.id),
                str(interaction.guild_id) if interaction.guild_id else None,
            )
            async with self.bot.chat_semaphore:
                async with self.bot.db_sessionmaker() as db:
                    result = await self._service(db).chat(
                        message, context_from_interaction(interaction)
                    )
            view = await build_voice_asset_view(
                db_sessionmaker=self.bot.db_sessionmaker,
                settings=self.bot.settings,
                result=result,
                requester_user_id=interaction.user.id,
            )
            if view is None:
                await send_interaction_result(
                    interaction, result, private=private, include_evidence=evidence
                )
            else:
                await send_interaction_result(
                    interaction,
                    result,
                    private=private,
                    include_evidence=evidence,
                    view=view,
                )
        except CooldownExceeded as exc:
            await interaction.followup.send(
                f"请求过快，请在 {exc.retry_after:.1f} 秒后重试。",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except AppError as exc:
            await interaction.followup.send(
                f"无法完成请求：{exc.message}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            correlation_id.reset(token)

    @app_commands.command(name="chat-reset", description="仅重置你自己的当前会话")
    async def chat_reset(self, interaction: discord.Interaction) -> None:
        async with self.bot.db_sessionmaker() as db:
            count = await self._service(db).reset(context_from_interaction(interaction))
        await interaction.response.send_message(
            "已重置你的当前会话。" if count else "当前没有可重置的会话。", ephemeral=True
        )

    @app_commands.command(name="chat-settings", description="查看当前可调整的对话设置")
    async def chat_settings(self, interaction: discord.Interaction) -> None:
        context = context_from_interaction(interaction)

        async def save(save_interaction: discord.Interaction, values: dict[str, float]) -> None:
            async with self.bot.db_sessionmaker() as db:
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
        if self.bot.user is None or not should_process_message(
            message, bot_user_id=self.bot.user.id, settings=self.bot.settings
        ):
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
                        result = await self._service(db).chat(
                            content, context_from_message(message)
                        )
            view = await build_voice_asset_view(
                db_sessionmaker=self.bot.db_sessionmaker,
                settings=self.bot.settings,
                result=result,
                requester_user_id=message.author.id,
            )
            if view is None:
                await send_message_result(message, result)
            else:
                await send_message_result(message, result, view=view)
        except CooldownExceeded as exc:
            await message.channel.send(
                f"请求过快，请在 {exc.retry_after:.1f} 秒后重试。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except AppError as exc:
            logger.info("Discord message request rejected code=%s", exc.code)
            await message.channel.send(
                f"无法完成请求：{exc.message}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            correlation_id.reset(token)
