from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.persona import UtteranceCreate
from app.services.attachment_import_service import AttachmentImportService
from app.services.language_analysis_service import LanguageAnalysisService
from app.services.persona_update_service import PersonaUpdateService
from bot.checks.admin import owner_only
from bot.modals.utterance_modal import UtteranceModal
from bot.views.confirmation_view import ConfirmationView


class UtteranceAdminCog(commands.Cog):
    utterance = app_commands.Group(name="utterance", description="言论证据管理")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @utterance.command(name="add", description="向草稿添加言论")
    @owner_only()
    async def add(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        text: str,
        verified: bool = False,
    ):
        async with self.bot.db_sessionmaker() as db:
            item = await PersonaUpdateService(db).add_utterance(
                UUID(persona_id), UUID(version_id), UtteranceCreate(text=text, verified=verified)
            )
        await interaction.response.send_message(f"已添加 `{item.id}`。", ephemeral=True)

    @utterance.command(name="list", description="列出版本言论")
    @owner_only()
    async def list_items(self, interaction: discord.Interaction, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            items = await UtteranceRepository(db).list_for_version(UUID(version_id))
        text = (
            "\n".join(
                f"`{item.id}` · {'✓' if item.verified else '○'} · {item.text[:80]}"
                for item in items[:20]
            )
            or "没有言论。"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @utterance.command(name="show", description="显示一条言论")
    @owner_only()
    async def show(self, interaction: discord.Interaction, utterance_id: str):
        async with self.bot.db_sessionmaker() as db:
            item = await UtteranceRepository(db).get(UUID(utterance_id))
        await interaction.response.send_message(
            item.text if item else "言论不存在。", ephemeral=True
        )

    async def _verify(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        utterance_id: str,
        verified: bool,
    ):
        async with self.bot.db_sessionmaker() as db:
            item = await PersonaUpdateService(db).set_verified(
                UUID(persona_id), UUID(version_id), UUID(utterance_id), verified
            )
        await interaction.response.send_message(
            f"`{item.id}` 已{'核验' if verified else '取消核验'}。", ephemeral=True
        )

    @utterance.command(name="verify", description="核验草稿言论")
    @owner_only()
    async def verify(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        utterance_id: str,
    ):
        await self._verify(interaction, persona_id, version_id, utterance_id, True)

    @utterance.command(name="unverify", description="取消核验草稿言论")
    @owner_only()
    async def unverify(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        utterance_id: str,
    ):
        await self._verify(interaction, persona_id, version_id, utterance_id, False)

    @utterance.command(name="delete", description="删除草稿言论")
    @owner_only()
    async def delete(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        utterance_id: str,
    ):
        async with self.bot.db_sessionmaker() as db:
            await PersonaUpdateService(db).delete_utterance(
                UUID(persona_id), UUID(version_id), UUID(utterance_id)
            )
        await interaction.response.send_message("已删除。", ephemeral=True)

    @utterance.command(name="edit", description="通过 Modal 编辑草稿言论")
    @owner_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        utterance_id: str,
    ):
        async with self.bot.db_sessionmaker() as db:
            await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
            item = await UtteranceRepository(db).get(UUID(utterance_id))
            if item is None or item.persona_version_id != UUID(version_id):
                await interaction.response.send_message("言论不存在。", ephemeral=True)
                return
            initial = {"text": item.text, "context": item.context, "topic": item.topic or ""}

        async def save(save_interaction: discord.Interaction, values: dict[str, str]) -> None:
            async with self.bot.db_sessionmaker() as db:
                await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
                target = await UtteranceRepository(db).get(UUID(utterance_id))
                target.text = values["text"]
                target.context = values["context"]
                target.topic = values["topic"] or None
                await db.commit()
            await save_interaction.response.send_message("言论已更新。", ephemeral=True)

        await interaction.response.send_modal(
            UtteranceModal(on_submit_callback=save, initial=initial)
        )

    @utterance.command(name="import", description="预览并导入 UTF-8 JSONL 或文本附件")
    @owner_only()
    async def import_attachment(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        attachment: discord.Attachment,
        verify: bool = False,
    ):
        data = await attachment.read(use_cached=True)
        async with self.bot.db_sessionmaker() as db:
            preview = await AttachmentImportService(db, self.bot.settings).preview(
                version_id=UUID(version_id),
                filename=attachment.filename,
                content_type=attachment.content_type,
                data=data,
                verify=verify,
            )

        async def confirm(confirm_interaction: discord.Interaction) -> None:
            async with self.bot.db_sessionmaker() as db:
                records = await AttachmentImportService(db, self.bot.settings).persist(
                    UUID(persona_id), UUID(version_id), preview
                )
            await confirm_interaction.response.edit_message(
                content=f"已导入 {len(records)} 条言论。", embed=None, view=None
            )

        embed = discord.Embed(title="导入预览", description=attachment.filename)
        embed.add_field(name="有效记录", value=str(len(preview.items)))
        embed.add_field(name="重复跳过", value=str(preview.duplicate_count))
        embed.add_field(name="核验状态", value="已核验" if verify else "待核验")
        embed.add_field(
            name="前 5 条",
            value="\n".join(item.text[:100] for item in preview.items[:5]),
            inline=False,
        )
        view = ConfirmationView(owner_user_id=interaction.user.id, on_confirm=confirm)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @utterance.command(name="analyze", description="分析已核验言论并提出规则建议")
    @owner_only()
    async def analyze(self, interaction: discord.Interaction, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            items = await UtteranceRepository(db).list_for_version(UUID(version_id), verified=True)
        analysis = LanguageAnalysisService().analyze(items)
        embed = discord.Embed(title="语言分析建议", description="这些建议尚未发布。")
        embed.add_field(
            name="高频短语",
            value="\n".join(
                f"{item['text']} · {item['count']}" for item in analysis.frequent_phrases[:10]
            )
            or "无",
        )
        embed.add_field(name="句长统计", value=str(analysis.sentence_length))
        embed.set_footer(text="必须由 Owner 明确审核后才能成为规则。")
        await interaction.response.send_message(embed=embed, ephemeral=True)
