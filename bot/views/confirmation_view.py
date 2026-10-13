from collections.abc import Awaitable, Callable

import discord


class ConfirmationView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_user_id: int,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_user_id = owner_user_id
        self.on_confirm_callback = on_confirm
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("只有发起者可以确认此操作。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="确认导入", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.confirmed or not await self.interaction_check(interaction):
            return
        self.confirmed = True
        await self.on_confirm_callback(interaction)
        for child in self.children:
            child.disabled = True
        self.stop()

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="已取消导入。", embed=None, view=None)
        self.stop()
