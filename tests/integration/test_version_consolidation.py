import pytest
from sqlalchemy import func, select

from app.db.models import (
    DiscordConversation,
    PersonaVersion,
    StyleProfile,
    Utterance,
)
from app.schemas.persona import PersonaCreate, UtteranceCreate, VersionCreate
from app.services.persona_update_service import PersonaUpdateService
from app.services.persona_version_consolidation_service import (
    PersonaVersionConsolidationService,
)


@pytest.mark.asyncio
async def test_consolidation_creates_v03_repoints_conversations_and_deletes_legacy(
    db_sessionmaker,
):
    async with db_sessionmaker() as db:
        update_service = PersonaUpdateService(db)
        persona, first = await update_service.create_persona(
            PersonaCreate(display_name="刁民", coverage_threshold=0.05)
        )
        await update_service.add_utterance(
            persona.id,
            first.id,
            UtteranceCreate(text="第一条粤语语料", verified=True),
        )
        await update_service.publish(persona.id, first.id)
        second = await update_service.create_draft(
            persona.id,
            VersionCreate(version="0.2.0", clone_from_version_id=first.id),
        )
        await update_service.add_utterance(
            persona.id,
            second.id,
            UtteranceCreate(text="第二条粤语语料", verified=True),
        )
        await update_service.publish(persona.id, second.id)
        db.add_all(
            [
                DiscordConversation(
                    persona_id=persona.id,
                    persona_version_id=first.id,
                    guild_id="guild",
                    channel_id="channel",
                    discord_user_id="legacy-user",
                ),
                DiscordConversation(
                    persona_id=persona.id,
                    persona_version_id=second.id,
                    guild_id="guild",
                    channel_id="channel",
                    discord_user_id="current-user",
                ),
            ]
        )
        await db.commit()

        report = await PersonaVersionConsolidationService(db).consolidate(
            persona_name="刁民",
            source_version="0.2.0",
            target_version="0.3.0",
            delete_versions={"0.1.0", "0.2.0"},
        )

        versions = list(
            await db.scalars(select(PersonaVersion).where(PersonaVersion.persona_id == persona.id))
        )
        assert [version.version for version in versions] == ["0.3.0"]
        target = versions[0]
        await db.refresh(persona)
        assert persona.active_version_id == target.id
        assert report.target_version_id == target.id
        assert report.utterance_count == 2
        assert report.verified_utterance_count == 2
        assert report.repointed_conversation_count == 2
        assert report.deleted_versions == ("0.1.0", "0.2.0")
        assert await db.get(StyleProfile, target.id) is not None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Utterance)
                .where(Utterance.persona_version_id == target.id)
            )
            == 2
        )
        conversations = list(
            await db.scalars(
                select(DiscordConversation).where(DiscordConversation.persona_id == persona.id)
            )
        )
        assert {conversation.persona_version_id for conversation in conversations} == {target.id}
