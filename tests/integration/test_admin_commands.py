from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.repositories.persona_repository import PersonaRepository
from app.repositories.utterance_repository import UtteranceRepository
from app.repositories.version_repository import VersionRepository
from bot.cogs.persona_admin import PersonaAdminCog
from bot.cogs.utterance_admin import UtteranceAdminCog


def interaction(*, guild_id=100):
    return SimpleNamespace(
        guild_id=guild_id,
        channel_id=200,
        response=SimpleNamespace(send_message=AsyncMock()),
        user=SimpleNamespace(id=1),
        client=SimpleNamespace(
            settings=Settings(
                discord_owner_user_ids="1", discord_guild_id="100", discord_chat_channel_id="200"
            )
        ),
    )


@pytest.mark.asyncio
async def test_persona_and_utterance_command_callbacks(db_sessionmaker):
    fake_bot = SimpleNamespace(db_sessionmaker=db_sessionmaker)
    persona_cog = PersonaAdminCog(fake_bot)
    utterance_cog = UtteranceAdminCog(fake_bot)
    current = interaction()

    await persona_cog.create.callback(persona_cog, current, "命令人物", "命令测试")
    assert "已创建" in current.response.send_message.await_args.args[0]
    async with db_sessionmaker() as db:
        persona = (await PersonaRepository(db).list())[0]
        first = (await VersionRepository(db).list_for_persona(persona.id))[0]

    await persona_cog.list_personas.callback(persona_cog, current)
    assert "命令人物" in current.response.send_message.await_args.args[0]
    await persona_cog.show.callback(persona_cog, current, str(persona.id))
    assert current.response.send_message.await_args.kwargs["embed"].title == "命令人物"

    await persona_cog.select_persona.callback(persona_cog, current, str(persona.id))
    assert "尚未发布" in current.response.send_message.await_args.args[0]
    await persona_cog.select_persona.callback(
        persona_cog, interaction(guild_id=None), str(persona.id)
    )

    await utterance_cog.add.callback(
        utterance_cog,
        current,
        str(persona.id),
        str(first.id),
        "找工作不顺时先做能控制的事。",
        False,
    )
    async with db_sessionmaker() as db:
        item = (await UtteranceRepository(db).list_for_version(first.id))[0]
    await utterance_cog.list_items.callback(utterance_cog, current, str(first.id))
    assert "找工作" in current.response.send_message.await_args.args[0]
    await utterance_cog.show.callback(utterance_cog, current, str(item.id))
    assert "找工作" in current.response.send_message.await_args.args[0]
    await utterance_cog.verify.callback(
        utterance_cog, current, str(persona.id), str(first.id), str(item.id)
    )
    await utterance_cog.unverify.callback(
        utterance_cog, current, str(persona.id), str(first.id), str(item.id)
    )
    await utterance_cog.verify.callback(
        utterance_cog, current, str(persona.id), str(first.id), str(item.id)
    )

    await persona_cog.publish.callback(persona_cog, current, str(persona.id), str(first.id))
    await persona_cog.select_persona.callback(persona_cog, current, str(persona.id))
    assert "已更新" in current.response.send_message.await_args.args[0]

    await persona_cog.clone_version.callback(persona_cog, current, str(persona.id), "1.0.0")
    async with db_sessionmaker() as db:
        versions = await VersionRepository(db).list_for_persona(persona.id)
        second = versions[-1]
    await persona_cog.publish.callback(persona_cog, current, str(persona.id), str(second.id))
    await persona_cog.activate.callback(persona_cog, current, str(persona.id), str(first.id))
    await persona_cog.rollback.callback(persona_cog, current, str(persona.id))
    await persona_cog.export.callback(persona_cog, current, str(persona.id))
    assert current.response.send_message.await_args.kwargs["file"].filename == "persona.json"

    await persona_cog.clone_version.callback(persona_cog, current, str(persona.id), "1.1.0")
    async with db_sessionmaker() as db:
        third = (await VersionRepository(db).list_for_persona(persona.id))[-1]
        cloned_item = (await UtteranceRepository(db).list_for_version(third.id))[0]
    await utterance_cog.delete.callback(
        utterance_cog,
        current,
        str(persona.id),
        str(third.id),
        str(cloned_item.id),
    )
    assert "已删除" in current.response.send_message.await_args.args[0]
