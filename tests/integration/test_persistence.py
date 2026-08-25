from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.db.models import AddressRule, DiscordConversation
from app.repositories.persona_repository import PersonaRepository
from app.repositories.rule_repository import RuleRepository
from app.repositories.utterance_repository import UtteranceRepository
from app.repositories.version_repository import VersionRepository
from app.schemas.persona import PersonaCreate, UtteranceCreate, VersionCreate
from app.services.persona_update_service import PersonaUpdateService


@pytest.mark.asyncio
async def test_persona_version_utterance_publish_clone_activate_rollback(db_sessionmaker):
    async with db_sessionmaker() as db:
        service = PersonaUpdateService(db)
        persona, first = await service.create_persona(
            PersonaCreate(display_name="测试人物", coverage_threshold=0.3)
        )
        utterance = await service.add_utterance(
            persona.id,
            first.id,
            UtteranceCreate(text="找工作不顺时，先处理能控制的事情。", verified=True),
        )
        published = await service.publish(persona.id, first.id)
        assert published.status == "published"
        assert (await PersonaRepository(db).get(persona.id)).active_version_id == first.id

        second = await service.create_draft(persona.id, VersionCreate(version="1.0.0"))
        cloned = await UtteranceRepository(db).list_for_version(second.id, verified=True)
        assert len(cloned) == 1
        await service.publish(persona.id, second.id)
        await service.activate(persona.id, first.id)
        rolled = await service.rollback(persona.id)
        assert rolled.id == second.id

        with pytest.raises(AppError, match="immutable"):
            await service.set_verified(persona.id, first.id, utterance.id, False)
        versions = await VersionRepository(db).list_for_persona(persona.id)
        assert [item.version for item in versions] == ["0.1.0", "1.0.0"]


@pytest.mark.asyncio
async def test_publish_requires_verified_and_draft_delete_rules(db_sessionmaker):
    async with db_sessionmaker() as db:
        service = PersonaUpdateService(db)
        persona, version = await service.create_persona(PersonaCreate(display_name="边界人物"))
        item = await service.add_utterance(
            persona.id, version.id, UtteranceCreate(text="未核验", verified=False)
        )
        with pytest.raises(AppError, match="verified"):
            await service.publish(persona.id, version.id)
        await service.delete_utterance(persona.id, version.id, item.id)
        assert await UtteranceRepository(db).list_for_version(version.id) == []
        empty_draft = await service.create_draft(persona.id, VersionCreate(version="0.2.0"))
        assert empty_draft.status == "draft"
        with pytest.raises(AppError, match="already exists"):
            await service.create_draft(persona.id, VersionCreate(version="0.2.0"))
        with pytest.raises(AppError, match="not found"):
            await service.get_persona(uuid4())


@pytest.mark.asyncio
async def test_discord_address_rule_repository_and_snowflake_storage(db_sessionmaker):
    async with db_sessionmaker() as db:
        service = PersonaUpdateService(db)
        persona, version = await service.create_persona(PersonaCreate(display_name="称呼人物"))
        rule = AddressRule(
            persona_version_id=version.id,
            guild_id="123456789012345678",
            discord_user_id="987654321098765432",
            address="老友",
            priority=20,
        )
        db.add(rule)
        conversation = DiscordConversation(
            persona_id=persona.id,
            persona_version_id=version.id,
            guild_id="123456789012345678",
            channel_id="222222222222222222",
            discord_user_id="987654321098765432",
        )
        db.add(conversation)
        await db.commit()
        rules = await RuleRepository(db).addresses(version.id)
        assert rules[0].discord_user_id == "987654321098765432"
        assert conversation.id != uuid4()
