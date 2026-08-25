from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import AddressRule, PersonalityProfile, StyleProfile
from app.services.persona_update_service import PersonaUpdateService
from bot.checks.admin import owner_only
from bot.modals.address_modal import AddressModal
from bot.modals.style_modal import StyleModal
from bot.views.pagination_view import PaginationView
from bot.views.personality_view import PersonalityView


class StyleAdminCog(commands.Cog):
    style = app_commands.Group(name="style", description="表达风格管理")
    personality = app_commands.Group(name="personality", description="互动人格管理")
    address = app_commands.Group(name="address", description="称呼规则管理")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @style.command(name="show", description="显示版本表达风格")
    @owner_only()
    async def style_show(self, interaction: discord.Interaction, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            profile = await db.get(StyleProfile, UUID(version_id))
        if profile is None:
            await interaction.response.send_message("风格配置不存在。", ephemeral=True)
            return
        embed = discord.Embed(title="表达风格")
        embed.add_field(name="句长", value=profile.sentence_length_preference)
        embed.add_field(name="段落", value=profile.paragraph_length_preference)
        embed.add_field(name="Emoji", value=profile.emoji_policy)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @style.command(name="edit", description="通过 Modal 编辑草稿表达风格")
    @owner_only()
    async def style_edit(self, interaction: discord.Interaction, persona_id: str, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
            profile = await db.get(StyleProfile, UUID(version_id))
            initial = {
                column.name: getattr(profile, column.name)
                for column in StyleProfile.__table__.columns
                if column.name != "persona_version_id"
            }

        async def save(save_interaction: discord.Interaction, values: dict) -> None:
            async with self.bot.db_sessionmaker() as db:
                await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
                target = await db.get(StyleProfile, UUID(version_id))
                for key, value in values.items():
                    setattr(target, key, value)
                await db.commit()
            await save_interaction.response.send_message("风格已保存。", ephemeral=True)

        await interaction.response.send_modal(StyleModal(on_submit_callback=save, initial=initial))

    @personality.command(name="show", description="显示版本人格默认值")
    @owner_only()
    async def personality_show(self, interaction: discord.Interaction, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            profile = await db.get(PersonalityProfile, UUID(version_id))
        if profile is None:
            await interaction.response.send_message("人格配置不存在。", ephemeral=True)
            return
        lines = [
            f"{column.name}: {getattr(profile, column.name):.1f}"
            for column in PersonalityProfile.__table__.columns
            if column.name not in {"persona_version_id", "conflict_style"}
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @personality.command(name="edit", description="用 0–10 选择器编辑草稿人格")
    @owner_only()
    async def personality_edit(
        self, interaction: discord.Interaction, persona_id: str, version_id: str
    ):
        async def save(save_interaction: discord.Interaction, values: dict[str, float]) -> None:
            async with self.bot.db_sessionmaker() as db:
                await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
                profile = await db.get(PersonalityProfile, UUID(version_id))
                for key, value in values.items():
                    if not hasattr(profile, key):
                        raise AppError("unknown_trait", "Unknown personality trait.")
                    setattr(profile, key, value)
                await db.commit()
            await save_interaction.response.send_message(
                "人格值已保存；系统安全上限仍会优先。", ephemeral=True
            )

        view = PersonalityView(owner_user_id=interaction.user.id, on_change=save)
        await interaction.response.send_message(
            "先选择人格维度，再选择 0–10 等级。", view=view, ephemeral=True
        )

    @address.command(name="add", description="通过 Modal 添加称呼规则")
    @owner_only()
    async def address_add(self, interaction: discord.Interaction, persona_id: str, version_id: str):
        async def save(save_interaction: discord.Interaction, values: dict) -> None:
            async with self.bot.db_sessionmaker() as db:
                await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
                db.add(
                    AddressRule(
                        persona_version_id=UUID(version_id),
                        guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                        **values,
                    )
                )
                await db.commit()
            await save_interaction.response.send_message("称呼规则已添加。", ephemeral=True)

        await interaction.response.send_modal(AddressModal(on_submit_callback=save))

    @address.command(name="list", description="分页列出称呼规则")
    @owner_only()
    async def address_list(self, interaction: discord.Interaction, version_id: str):
        async with self.bot.db_sessionmaker() as db:
            rules = list(
                await db.scalars(
                    select(AddressRule).where(AddressRule.persona_version_id == UUID(version_id))
                )
            )
        lines = [
            f"`{rule.id}` · user={rule.discord_user_id or '-'} · "
            f"role={rule.discord_role_id or '-'} → {rule.address}"
            for rule in rules
        ]
        pages = ["\n".join(lines[index : index + 10]) for index in range(0, len(lines), 10)]
        view = PaginationView(pages, owner_user_id=interaction.user.id)
        await interaction.response.send_message(view.content, view=view, ephemeral=True)

    @address.command(name="edit", description="通过 Modal 编辑草稿称呼规则")
    @owner_only()
    async def address_edit(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        rule_id: str,
    ):
        async with self.bot.db_sessionmaker() as db:
            await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
            rule = await db.get(AddressRule, UUID(rule_id))
            if rule is None or rule.persona_version_id != UUID(version_id):
                raise AppError("rule_not_found", "Address rule not found.")
            initial = {
                key: getattr(rule, key)
                for key in (
                    "address",
                    "discord_user_id",
                    "discord_role_id",
                    "relationship",
                    "priority",
                )
            }

        async def save(save_interaction: discord.Interaction, values: dict) -> None:
            async with self.bot.db_sessionmaker() as db:
                await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
                target = await db.get(AddressRule, UUID(rule_id))
                for key, value in values.items():
                    setattr(target, key, value)
                await db.commit()
            await save_interaction.response.send_message("称呼规则已更新。", ephemeral=True)

        await interaction.response.send_modal(
            AddressModal(on_submit_callback=save, initial=initial)
        )

    @address.command(name="delete", description="删除草稿称呼规则")
    @owner_only()
    async def address_delete(
        self,
        interaction: discord.Interaction,
        persona_id: str,
        version_id: str,
        rule_id: str,
    ):
        async with self.bot.db_sessionmaker() as db:
            await PersonaUpdateService(db).require_draft(UUID(persona_id), UUID(version_id))
            rule = await db.get(AddressRule, UUID(rule_id))
            if rule is None or rule.persona_version_id != UUID(version_id):
                raise AppError("rule_not_found", "Address rule not found.")
            await db.delete(rule)
            await db.commit()
        await interaction.response.send_message("称呼规则已删除。", ephemeral=True)
