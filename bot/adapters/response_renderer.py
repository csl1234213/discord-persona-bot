import re

import discord

from app.schemas.chat import ChatResult

MENTION_PATTERN = re.compile(r"@(everyone|here)|<@&?\d+>|<@!\d+>", re.IGNORECASE)


def neutralize_mentions(text: str) -> str:
    return MENTION_PATTERN.sub(lambda match: match.group(0).replace("@", "@\u200b"), text)


def split_discord_text(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            split_at = paragraph.rfind("\n", 0, limit)
            if split_at < limit // 2:
                split_at = paragraph.rfind("。", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            chunks.append(paragraph[:split_at].strip())
            paragraph = paragraph[split_at:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    chunks = [chunk for chunk in chunks if chunk]
    balanced: list[str] = []
    code_open = False
    for chunk in chunks:
        rendered = f"```\n{chunk}" if code_open else chunk
        if chunk.count("```") % 2:
            code_open = not code_open
        if code_open and len(rendered) + 4 <= limit:
            rendered += "\n```"
        balanced.append(rendered[:limit])
    return balanced


def evidence_embed(result: ChatResult) -> discord.Embed:
    color = 0x274C77 if result.boundary_status == "supported" else 0xD86852
    embed = discord.Embed(
        title=f"证据 · {result.persona_name} v{result.persona_version}",
        color=color,
    )
    if result.evidence:
        for item in result.evidence[:5]:
            embed.add_field(
                name=f"{str(item.utterance_id)[:8]} · {item.score:.2f}",
                value=item.excerpt[:500],
                inline=False,
            )
    else:
        embed.description = "没有达到覆盖阈值的已核验言论。"
    return embed


async def send_interaction_result(
    interaction: discord.Interaction,
    result: ChatResult,
    *,
    private: bool,
    include_evidence: bool,
    view: discord.ui.View | None = None,
    disclose: bool = False,
) -> None:
    answer = result.answer
    if disclose:
        answer += "\n\n（AI 風格模擬，並非本人。）"
    chunks = split_discord_text(neutralize_mentions(answer))
    embed = evidence_embed(result) if include_evidence else None
    for index, chunk in enumerate(chunks):
        kwargs = {
            "embed": embed if index == 0 else None,
            "ephemeral": private,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if index == 0 and view is not None:
            kwargs["view"] = view
            kwargs["wait"] = True
        sent_message = await interaction.followup.send(chunk, **kwargs)
        if index == 0 and view is not None:
            view.message = sent_message


async def send_message_result(
    message: discord.Message,
    result: ChatResult,
    *,
    view: discord.ui.View | None = None,
    disclose: bool = False,
) -> None:
    answer = result.answer
    if disclose:
        answer += "\n\n（AI 風格模擬，並非本人。）"
    chunks = split_discord_text(neutralize_mentions(answer))
    embed = (
        None
        if not result.evidence
        or result.response_origin in {"DIRECT_UTTERANCE", "POLITICAL_CORPUS_UTTERANCE"}
        else evidence_embed(result)
    )
    is_guild_message = message.guild is not None
    prefix = f"<@{message.author.id}> " if is_guild_message else ""
    allowed_mentions = discord.AllowedMentions.none()
    for index, chunk in enumerate(chunks):
        kwargs = {
            "embed": embed if index == 0 else None,
            "allowed_mentions": allowed_mentions,
        }
        if index == 0 and view is not None:
            kwargs["view"] = view
        sent_message = await message.channel.send(
            f"{prefix}{chunk}" if index == 0 else chunk, **kwargs
        )
        if index == 0 and view is not None:
            view.message = sent_message
