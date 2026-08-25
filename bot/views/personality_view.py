from collections.abc import Awaitable, Callable

import discord

TRAITS = {
    "directness": "直接",
    "warmth": "温暖",
    "humor": "幽默",
    "sarcasm": "讽刺",
    "patience": "耐心",
    "empathy": "共情",
    "assertiveness": "坚定",
    "skepticism": "质疑",
    "formality": "正式",
    "verbosity": "详略",
    "emotional_intensity": "情绪强度",
}


class TraitSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="选择人格维度",
            options=[
                discord.SelectOption(label=label, value=value) for value, label in TRAITS.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PersonalityView = self.view
        view.selected_trait = self.values[0]
        await interaction.response.defer()


class LevelSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="选择等级 0–10",
            options=[
                discord.SelectOption(label=str(level), value=str(level)) for level in range(11)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PersonalityView = self.view
        value = int(self.values[0]) / 10
        await view.on_change(interaction, {view.selected_trait: value})


class PersonalityView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_user_id: int,
        on_change: Callable[[discord.Interaction, dict[str, float]], Awaitable[None]],
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_user_id = owner_user_id
        self.on_change = on_change
        self.selected_trait = "warmth"
        self.add_item(TraitSelect())
        self.add_item(LevelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("只有发起者可以修改设置。", ephemeral=True)
            return False
        return True
