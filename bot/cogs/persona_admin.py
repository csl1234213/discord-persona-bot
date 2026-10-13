import io
import json
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from app.db.models import Persona
from app.repositories.persona_repository import PersonaRepository
from app.repositories.version_repository import VersionRepository
from app.schemas.persona import PersonaCreate, VersionCreate
from app.services.persona_update_service import PersonaUpdateService
from bot.checks.admin import owner_only, owner_or_guild_admin
from bot.checks.security import admin_session


class PersonaAdminCog(commands.Cog):
    persona = app_commands.Group(name="persona", description="人物与版本管理")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @persona.command(name="create", description="创建人物和初始草稿")
    @owner_only()
    async def create(self, interaction: discord.Interaction, name: str, description: str = ""):
        async with admin_session(self.bot, interaction) as db:
            persona, version = await PersonaUpdateService(db).create_persona(
                PersonaCreate(display_name=name, description=description)
            )
        await interaction.response.send_message(
            f"已创建 {persona.display_name}，草稿 v{version.version}。", ephemeral=True
        )

    @persona.command(name="list", description="列出人物")
    @owner_or_guild_admin()
    async def list_personas(self, interaction: discord.Interaction):
        async with admin_session(self.bot, interaction) as db:
            personas = await PersonaRepository(db).list()
        text = "\n".join(f"`{item.id}` · {item.display_name}" for item in personas) or "尚无人物。"
        await interaction.response.send_message(text, ephemeral=True)

    @persona.command(name="select", description="为当前服务器选择已发布人物")
    @owner_or_guild_admin()
    async def select_persona(self, interaction: discord.Interaction, persona_id: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("此命令仅用于服务器。", ephemeral=True)
            return
        async with admin_session(self.bot, interaction) as db:
            persona = await PersonaRepository(db).get(UUID(persona_id))
            if persona is None or persona.active_version_id is None:
                await interaction.response.send_message("人物不存在或尚未发布。", ephemeral=True)
                return
            await PersonaUpdateService(db).select_for_guild(persona.id, str(interaction.guild_id))
        await interaction.response.send_message("服务器人物已更新。", ephemeral=True)

    @persona.command(name="show", description="显示人物与版本")
    @owner_or_guild_admin()
    async def show(self, interaction: discord.Interaction, persona_id: str):
        async with admin_session(self.bot, interaction) as db:
            persona = await PersonaRepository(db).get(UUID(persona_id))
            versions = (
                await VersionRepository(db).list_for_persona(UUID(persona_id)) if persona else []
            )
        if persona is None:
            await interaction.response.send_message("人物不存在。", ephemeral=True)
            return
        embed = discord.Embed(title=persona.display_name, description=persona.description)
        embed.add_field(
            name="版本",
            value="\n".join(f"{item.version} · {item.status}" for item in versions),
        )
        embed.set_footer(text=persona.disclosure)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @persona.command(name="clone-version", description="从当前发布版克隆草稿")
    @owner_only()
    async def clone_version(self, interaction: discord.Interaction, persona_id: str, version: str):
        async with admin_session(self.bot, interaction) as db:
            draft = await PersonaUpdateService(db).create_draft(
                UUID(persona_id), VersionCreate(version=version, change_notes="Discord clone")
            )
        await interaction.response.send_message(f"已创建草稿 v{draft.version}。", ephemeral=True)

    @persona.command(name="publish", description="发布草稿并用于新会话")
    @owner_only()
    async def publish(self, interaction: discord.Interaction, persona_id: str, version_id: str):
        async with admin_session(self.bot, interaction) as db:
            version = await PersonaUpdateService(db).publish(UUID(persona_id), UUID(version_id))
        await interaction.response.send_message(f"已发布 v{version.version}。", ephemeral=True)

    @persona.command(name="activate", description="激活历史发布版本")
    @owner_only()
    async def activate(self, interaction: discord.Interaction, persona_id: str, version_id: str):
        async with admin_session(self.bot, interaction) as db:
            version = await PersonaUpdateService(db).activate(UUID(persona_id), UUID(version_id))
        await interaction.response.send_message(f"已激活 v{version.version}。", ephemeral=True)

    @persona.command(name="rollback", description="回滚到上一发布版本")
    @owner_only()
    async def rollback(self, interaction: discord.Interaction, persona_id: str):
        async with admin_session(self.bot, interaction) as db:
            version = await PersonaUpdateService(db).rollback(UUID(persona_id))
        await interaction.response.send_message(f"已回滚到 v{version.version}。", ephemeral=True)

    @persona.command(name="export", description="导出人物元数据（不含秘密）")
    @owner_only()
    async def export(self, interaction: discord.Interaction, persona_id: str):
        async with admin_session(self.bot, interaction) as db:
            persona: Persona | None = await PersonaRepository(db).get(UUID(persona_id))
            versions = (
                await VersionRepository(db).list_for_persona(UUID(persona_id)) if persona else []
            )
        if persona is None:
            await interaction.response.send_message("人物不存在。", ephemeral=True)
            return
        payload = {
            "id": str(persona.id),
            "display_name": persona.display_name,
            "description": persona.description,
            "versions": [item.version for item in versions],
        }
        file = discord.File(
            io.BytesIO(json.dumps(payload, ensure_ascii=False).encode()), "persona.json"
        )
        await interaction.response.send_message(file=file, ephemeral=True)
