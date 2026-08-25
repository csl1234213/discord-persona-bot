from collections.abc import Awaitable, Callable

import discord


class AddressModal(discord.ui.Modal, title="称呼规则"):
    address = discord.ui.TextInput(label="称呼", max_length=120)
    discord_user_id = discord.ui.TextInput(label="Discord 用户 ID", required=False, max_length=24)
    discord_role_id = discord.ui.TextInput(label="Discord 角色 ID", required=False, max_length=24)
    relationship = discord.ui.TextInput(label="关系", required=False, max_length=80)
    priority = discord.ui.TextInput(label="优先级", default="0", max_length=4)

    def __init__(
        self,
        *,
        on_submit_callback: Callable[[discord.Interaction, dict], Awaitable[None]],
        initial: dict | None = None,
    ) -> None:
        super().__init__()
        self.on_submit_callback = on_submit_callback
        initial = initial or {}
        for key in ("address", "discord_user_id", "discord_role_id", "relationship", "priority"):
            value = initial.get(key)
            if value is not None:
                getattr(self, key).default = str(value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            priority = int(str(self.priority))
        except ValueError:
            await interaction.response.send_message("优先级必须是整数。", ephemeral=True)
            return
        await self.on_submit_callback(
            interaction,
            {
                "address": str(self.address),
                "discord_user_id": str(self.discord_user_id).strip() or None,
                "discord_role_id": str(self.discord_role_id).strip() or None,
                "relationship": str(self.relationship).strip() or None,
                "priority": priority,
            },
        )
