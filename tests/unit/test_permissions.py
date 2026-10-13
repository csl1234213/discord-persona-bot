from types import SimpleNamespace

import pytest
from discord import app_commands

from app.core.config import Settings
from bot.checks.admin import (
    interaction_is_guild_admin,
    is_owner,
    owner_only,
    owner_or_guild_admin,
)
from bot.checks.guild_scope import allowed_guild_only


def test_owner_and_guild_admin_boundaries():
    settings = Settings(discord_owner_user_ids="10,20")
    assert is_owner(settings, 10)
    assert not is_owner(settings, 30)
    assert interaction_is_guild_admin(
        SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)))
    )
    assert not interaction_is_guild_admin(SimpleNamespace(user=SimpleNamespace()))


@pytest.mark.asyncio
async def test_ordinary_user_fails_owner_check():
    decorator = owner_only()

    async def callback(interaction):
        return True

    command = app_commands.Command(name="test-owner", description="test", callback=callback)
    decorator(command)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=999),
        client=SimpleNamespace(settings=Settings(discord_owner_user_ids="1")),
    )
    with pytest.raises(app_commands.CheckFailure):
        await command.checks[0](interaction)


@pytest.mark.asyncio
async def test_owner_admin_and_guild_scope_decorators():
    async def callback(interaction):
        return True

    admin_command = app_commands.Command(name="test-admin", description="test", callback=callback)
    owner_or_guild_admin()(admin_command)
    admin_interaction = SimpleNamespace(
        user=SimpleNamespace(id=999, guild_permissions=SimpleNamespace(manage_guild=True)),
        client=SimpleNamespace(settings=Settings(discord_owner_user_ids="1")),
    )
    with pytest.raises(app_commands.CheckFailure):
        await admin_command.checks[0](admin_interaction)
    admin_interaction.user.id = 1
    assert await admin_command.checks[0](admin_interaction)
    admin_interaction.user.id = 999
    admin_interaction.user.guild_permissions.manage_guild = False
    with pytest.raises(app_commands.CheckFailure):
        await admin_command.checks[0](admin_interaction)

    guild_command = app_commands.Command(name="test-guild", description="test", callback=callback)
    allowed_guild_only()(guild_command)
    allowed = SimpleNamespace(
        guild_id=10,
        client=SimpleNamespace(settings=Settings(discord_allowed_guild_ids="10")),
    )
    assert await guild_command.checks[0](allowed)
    allowed.guild_id = 11
    with pytest.raises(app_commands.CheckFailure):
        await guild_command.checks[0](allowed)
