from collections.abc import Awaitable, Callable

import discord


class UtteranceModal(discord.ui.Modal, title="编辑言论"):
    text = discord.ui.TextInput(label="言论", style=discord.TextStyle.paragraph, max_length=4000)
    context = discord.ui.TextInput(
        label="上下文", style=discord.TextStyle.paragraph, required=False, max_length=1000
    )
    topic = discord.ui.TextInput(label="主题", required=False, max_length=200)

    def __init__(
        self,
        *,
        on_submit_callback: Callable[[discord.Interaction, dict[str, str]], Awaitable[None]],
        initial: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.on_submit_callback = on_submit_callback
        initial = initial or {}
        self.text.default = initial.get("text", "")
        self.context.default = initial.get("context", "")
        self.topic.default = initial.get("topic", "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_submit_callback(
            interaction,
            {"text": str(self.text), "context": str(self.context), "topic": str(self.topic)},
        )
