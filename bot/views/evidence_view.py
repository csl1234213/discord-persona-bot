import discord

from app.schemas.chat import ChatResult
from bot.adapters.response_renderer import evidence_embed


class EvidenceView(discord.ui.View):
    def __init__(self, result: ChatResult, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.result = result

    @discord.ui.button(label="查看证据", style=discord.ButtonStyle.secondary)
    async def show_evidence(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(embed=evidence_embed(self.result), ephemeral=True)
