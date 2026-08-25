from collections.abc import Sequence

import discord


class PaginationView(discord.ui.View):
    def __init__(self, pages: Sequence[str], *, owner_user_id: int, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.pages = list(pages) or ["没有记录。"]
        self.owner_user_id = owner_user_id
        self.index = 0
        self._update_buttons()

    @property
    def content(self) -> str:
        return f"{self.pages[self.index]}\n\n第 {self.index + 1}/{len(self.pages)} 页"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("只有发起者可以翻页。", ephemeral=True)
            return False
        return True

    def _update_buttons(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    @discord.ui.button(label="上一页", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.content, view=self)

    @discord.ui.button(label="下一页", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.content, view=self)
