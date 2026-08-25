from collections.abc import Awaitable, Callable

import discord


class StyleModal(discord.ui.Modal, title="编辑表达风格"):
    sentence_length = discord.ui.TextInput(
        label="句长偏好", placeholder="short / medium / long", max_length=20
    )
    paragraph_length = discord.ui.TextInput(
        label="段落偏好", placeholder="short / medium / long", max_length=20
    )
    emoji_policy = discord.ui.TextInput(
        label="Emoji 策略", placeholder="never / rare / allowed", max_length=20
    )
    opening_patterns = discord.ui.TextInput(
        label="常见开头（每行一个）", style=discord.TextStyle.paragraph, required=False
    )

    def __init__(
        self,
        *,
        on_submit_callback: Callable[[discord.Interaction, dict], Awaitable[None]],
        initial: dict | None = None,
    ) -> None:
        super().__init__()
        self.on_submit_callback = on_submit_callback
        initial = initial or {}
        self.sentence_length.default = initial.get("sentence_length_preference", "medium")
        self.paragraph_length.default = initial.get("paragraph_length_preference", "medium")
        self.emoji_policy.default = initial.get("emoji_policy", "rare")
        self.opening_patterns.default = "\n".join(initial.get("opening_patterns", []))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_submit_callback(
            interaction,
            {
                "sentence_length_preference": str(self.sentence_length),
                "paragraph_length_preference": str(self.paragraph_length),
                "emoji_policy": str(self.emoji_policy),
                "opening_patterns": [
                    item.strip() for item in str(self.opening_patterns).splitlines() if item.strip()
                ],
            },
        )
