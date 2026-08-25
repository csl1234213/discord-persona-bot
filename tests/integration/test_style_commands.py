from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import AddressRule, StyleProfile
from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.persona_update_service import PersonaUpdateService
from bot.cogs.style_admin import StyleAdminCog
from bot.cogs.utterance_admin import UtteranceAdminCog
from bot.modals.address_modal import AddressModal
from bot.modals.style_modal import StyleModal
from bot.modals.utterance_modal import UtteranceModal
from bot.views.pagination_view import PaginationView
from bot.views.personality_view import PersonalityView


def interaction():
    return SimpleNamespace(
        guild_id=10,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_style_personality_address_and_utterance_modal_commands(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="配置人物")
        )
        utterance = await PersonaUpdateService(db).add_utterance(
            persona.id, version.id, UtteranceCreate(text="原始言论")
        )
    bot = SimpleNamespace(db_sessionmaker=db_sessionmaker)
    style_cog = StyleAdminCog(bot)
    utterance_cog = UtteranceAdminCog(bot)
    current = interaction()

    await style_cog.style_show.callback(style_cog, current, str(version.id))
    assert current.response.send_message.await_args.kwargs["embed"].title == "表达风格"
    await style_cog.style_edit.callback(style_cog, current, str(persona.id), str(version.id))
    style_modal = current.response.send_modal.await_args.args[0]
    assert isinstance(style_modal, StyleModal)
    save_interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    await style_modal.on_submit_callback(
        save_interaction,
        {
            "sentence_length_preference": "short",
            "paragraph_length_preference": "short",
            "emoji_policy": "never",
            "opening_patterns": ["先说"],
        },
    )
    async with db_sessionmaker() as db:
        assert (await db.get(StyleProfile, version.id)).emoji_policy == "never"

    await style_cog.personality_show.callback(style_cog, current, str(version.id))
    assert "warmth" in current.response.send_message.await_args.args[0]
    await style_cog.personality_edit.callback(style_cog, current, str(persona.id), str(version.id))
    personality_view = current.response.send_message.await_args.kwargs["view"]
    assert isinstance(personality_view, PersonalityView)
    await personality_view.on_change(save_interaction, {"warmth": 0.9})

    await style_cog.address_add.callback(style_cog, current, str(persona.id), str(version.id))
    address_modal = current.response.send_modal.await_args.args[0]
    assert isinstance(address_modal, AddressModal)
    await address_modal.on_submit_callback(
        save_interaction,
        {
            "address": "老友",
            "discord_user_id": "123",
            "discord_role_id": None,
            "relationship": None,
            "priority": 10,
        },
    )
    async with db_sessionmaker() as db:
        rule = (await db.scalars(select(AddressRule))).first()

    await style_cog.address_list.callback(style_cog, current, str(version.id))
    assert isinstance(current.response.send_message.await_args.kwargs["view"], PaginationView)
    await style_cog.address_edit.callback(
        style_cog, current, str(persona.id), str(version.id), str(rule.id)
    )
    edit_modal = current.response.send_modal.await_args.args[0]
    await edit_modal.on_submit_callback(
        save_interaction,
        {
            "address": "朋友",
            "discord_user_id": "123",
            "discord_role_id": None,
            "relationship": None,
            "priority": 11,
        },
    )
    await style_cog.address_delete.callback(
        style_cog, current, str(persona.id), str(version.id), str(rule.id)
    )

    await utterance_cog.edit.callback(
        utterance_cog, current, str(persona.id), str(version.id), str(utterance.id)
    )
    utterance_modal = current.response.send_modal.await_args.args[0]
    assert isinstance(utterance_modal, UtteranceModal)
    await utterance_modal.on_submit_callback(
        save_interaction, {"text": "更新言论", "context": "场景", "topic": "主题"}
    )
    async with db_sessionmaker() as db:
        assert (await UtteranceRepository(db).get(utterance.id)).text == "更新言论"
