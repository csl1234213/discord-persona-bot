import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord import app_commands

from app.core.config import Settings
from app.core.errors import AppError
from bot.checks.cooldown import CooldownExceeded, CooldownManager
from bot.client import create_bot
from bot.modals.address_modal import AddressModal
from bot.modals.style_modal import StyleModal
from bot.modals.utterance_modal import UtteranceModal
from bot.views.pagination_view import PaginationView
from bot.views.personality_view import PersonalityView


@pytest.mark.asyncio
async def test_cooldown_is_per_user_and_does_not_reject_other_guild_members():
    manager = CooldownManager(user_seconds=10, guild_seconds=5)
    await asyncio.gather(*(manager.acquire(f"user-{index}", "guild-1") for index in range(100)))
    with pytest.raises(CooldownExceeded) as user_error:
        await manager.acquire("user-1", "guild-2")
    assert user_error.value.retry_after > 0
    await CooldownManager(0, 0).acquire("user", None)


@pytest.mark.asyncio
async def test_command_error_handler_uses_safe_response():
    bot = create_bot(
        Settings(
            discord_sync_commands_on_startup=False,
            database_url="sqlite+aiosqlite:///./.cache/tests/error-handler.sqlite3",
        )
    )
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=SimpleNamespace(send=AsyncMock()))
    await bot.on_app_command_error(
        interaction,
        SimpleNamespace(original=AppError("safe", "可安全显示")),
    )
    assert "可安全显示" in response.send_message.await_args.args[0]
    await bot.on_app_command_error(interaction, app_commands.CheckFailure("hidden"))
    assert "权限" in response.send_message.await_args.args[0]
    interaction.response = SimpleNamespace(is_done=lambda: True, send_message=AsyncMock())
    await bot.on_app_command_error(interaction, app_commands.AppCommandError("internal"))
    assert "稍后重试" in interaction.followup.send.await_args.args[0]
    await bot.close()


@pytest.mark.asyncio
async def test_component_views_restrict_owner_and_apply_normalized_level():
    changes = AsyncMock()
    personality = PersonalityView(owner_user_id=1, on_change=changes)
    denied = SimpleNamespace(
        user=SimpleNamespace(id=2),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert not await personality.interaction_check(denied)
    allowed = SimpleNamespace(user=SimpleNamespace(id=1))
    assert await personality.interaction_check(allowed)
    trait_select, level_select = personality.children
    trait_select._values = ["warmth"]
    level_select._values = ["8"]
    trait_interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
    await trait_select.callback(trait_interaction)
    await level_select.callback(trait_interaction)
    changes.assert_awaited_with(trait_interaction, {"warmth": 0.8})

    pagination = PaginationView(["one", "two"], owner_user_id=1)
    assert "第 1/2 页" in pagination.content
    assert not await pagination.interaction_check(denied)


@pytest.mark.asyncio
async def test_modals_forward_typed_values_and_validate_priority():
    utterance_callback = AsyncMock()
    utterance = UtteranceModal(
        on_submit_callback=utterance_callback,
        initial={"text": "原文", "context": "上下文", "topic": "主题"},
    )
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    await utterance.on_submit(interaction)
    utterance_callback.assert_awaited_once()

    style_callback = AsyncMock()
    style = StyleModal(on_submit_callback=style_callback)
    await style.on_submit(interaction)
    assert "sentence_length_preference" in style_callback.await_args.args[1]

    address_callback = AsyncMock()
    address = AddressModal(on_submit_callback=address_callback)
    address.address._value = "老友"
    address.priority._value = "not-int"
    await address.on_submit(interaction)
    assert "整数" in interaction.response.send_message.await_args.args[0]
    address.priority._value = "10"
    await address.on_submit(interaction)
    assert address_callback.await_args.args[1]["priority"] == 10


def test_style_validator_is_bounded():
    from app.validators.style_validator import StyleValidator

    assert StyleValidator().validate_length("ok")
    assert not StyleValidator().validate_length("")
    assert not StyleValidator().validate_length("x" * 8001)
