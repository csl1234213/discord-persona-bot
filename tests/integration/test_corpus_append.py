import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db.models import DiscordConversation, PersonaVersion, Utterance
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.persona_corpus_append_service import PersonaCorpusAppendService
from app.services.persona_update_service import PersonaUpdateService


async def seed_v03(db):
    update_service = PersonaUpdateService(db)
    persona, version = await update_service.create_persona(
        PersonaCreate(display_name="刁民", coverage_threshold=0.05)
    )
    version.version = "0.3.0"
    await update_service.add_utterance(
        persona.id,
        version.id,
        UtteranceCreate(text="原始语料一", verified=True),
    )
    await update_service.add_utterance(
        persona.id,
        version.id,
        UtteranceCreate(text="原始语料二", verified=True),
    )
    await update_service.publish(persona.id, version.id)
    return persona, version


@pytest.mark.asyncio
async def test_append_clones_source_adds_only_unique_lines_and_repoints_conversations(
    db_sessionmaker,
):
    async with db_sessionmaker() as db:
        persona, source = await seed_v03(db)
        db.add(
            DiscordConversation(
                persona_id=persona.id,
                persona_version_id=source.id,
                guild_id="guild",
                channel_id="channel",
                discord_user_id="user",
            )
        )
        await db.commit()
        source_before = list(
            await db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == source.id)
                .order_by(Utterance.created_at, Utterance.id)
            )
        )
        source_snapshot = [(item.id, item.text, item.verified) for item in source_before]

        report = await PersonaCorpusAppendService(db, Settings()).append_text_file(
            persona_name="刁民",
            target_version="0.4.0",
            filename="append.txt",
            data="原始语料一\n新增语料甲\n新增语料甲\n新增语料乙\n".encode(),
        )

        versions = list(
            await db.scalars(
                select(PersonaVersion)
                .where(PersonaVersion.persona_id == persona.id)
                .order_by(PersonaVersion.version)
            )
        )
        assert [version.version for version in versions] == ["0.3.0", "0.4.0"]
        target = versions[1]
        assert target.status == "published"
        await db.refresh(persona)
        assert persona.active_version_id == target.id
        assert report.source_utterance_count == 2
        assert report.appended_utterance_count == 2
        assert report.duplicate_count == 2
        assert report.target_utterance_count == 4
        assert report.target_verified_utterance_count == 4
        assert report.repointed_conversation_count == 1

        source_after = list(
            await db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == source.id)
                .order_by(Utterance.created_at, Utterance.id)
            )
        )
        assert [(item.id, item.text, item.verified) for item in source_after] == source_snapshot
        target_items = list(
            await db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == target.id)
                .order_by(Utterance.text)
            )
        )
        assert {item.text for item in target_items} == {
            "原始语料一",
            "原始语料二",
            "新增语料甲",
            "新增语料乙",
        }
        assert all(item.verified for item in target_items)
        conversation = await db.scalar(
            select(DiscordConversation).where(DiscordConversation.persona_id == persona.id)
        )
        assert conversation.persona_version_id == target.id


@pytest.mark.asyncio
async def test_all_duplicate_append_rolls_back_without_new_version(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, _ = await seed_v03(db)
        persona_id = persona.id
        with pytest.raises(ValidationFailed):
            await PersonaCorpusAppendService(db, Settings()).append_text_file(
                persona_name="刁民",
                target_version="0.4.0",
                filename="duplicates.txt",
                data="原始语料一\n原始语料二\n".encode(),
            )
        versions = list(
            await db.scalars(select(PersonaVersion).where(PersonaVersion.persona_id == persona_id))
        )
        assert [version.version for version in versions] == ["0.3.0"]
