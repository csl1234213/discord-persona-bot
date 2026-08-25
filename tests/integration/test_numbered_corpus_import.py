import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import DiscordConversation, PersonaVersion, StyleProfile, Utterance
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.numbered_corpus_import_service import NumberedCorpusImportService
from app.services.persona_update_service import PersonaUpdateService


async def seed(db):
    updates = PersonaUpdateService(db)
    persona, source = await updates.create_persona(
        PersonaCreate(display_name="刁民", coverage_threshold=0.05)
    )
    await updates.add_utterance(
        persona.id,
        source.id,
        UtteranceCreate(text="旧语料", verified=True),
    )
    await updates.publish(persona.id, source.id)
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
    return persona, source


@pytest.mark.asyncio
async def test_preview_is_read_only_and_reports_numbered_duplicate_text(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, source = await seed(db)
        preview = await NumberedCorpusImportService(db).preview(
            persona_name="刁民",
            target_version="0.6.0",
            data="相同\n\n中间\n\n相同".encode(),
            expected_blocks=3,
        )
        versions = list(
            await db.scalars(select(PersonaVersion).where(PersonaVersion.persona_id == persona.id))
        )
        assert [item.id for item in versions] == [source.id]
        assert preview.block_count == 3
        assert preview.unique_text_count == 2
        assert preview.duplicate_text_block_groups == ((1, 3),)
        assert preview.planned_database_changes == 4


@pytest.mark.asyncio
async def test_apply_replaces_target_corpus_with_unique_numbered_references(
    db_sessionmaker,
):
    async with db_sessionmaker() as db:
        persona, source = await seed(db)
        service = NumberedCorpusImportService(db)
        report = await service.apply(
            persona_name="刁民",
            target_version="0.6.0",
            data="相同\n\n中间\n\n相同".encode(),
            expected_blocks=3,
        )
        rows = list(
            await db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == report.target_version_id)
                .order_by(Utterance.source_reference)
            )
        )
        source_rows = list(
            await db.scalars(select(Utterance).where(Utterance.persona_version_id == source.id))
        )
        conversation = await db.scalar(select(DiscordConversation))
        await db.refresh(persona)
        assert [item.text for item in rows] == ["相同", "中间", "相同"]
        assert [item.source_reference for item in rows] == [
            "voice_pair:001",
            "voice_pair:002",
            "voice_pair:003",
        ]
        assert all(item.verified for item in rows)
        assert [item.text for item in source_rows] == ["旧语料"]
        assert persona.active_version_id == report.target_version_id
        assert conversation is not None
        assert conversation.persona_version_id == report.target_version_id
        assert await db.get(StyleProfile, report.target_version_id) is not None

        repeated = await service.apply(
            persona_name="刁民",
            target_version="0.6.0",
            data="相同\n\n中间\n\n相同".encode(),
            expected_blocks=3,
        )
        assert repeated.already_applied is True
        assert repeated.target_version_id == report.target_version_id


@pytest.mark.asyncio
async def test_existing_mismatched_target_is_a_conflict(db_sessionmaker):
    async with db_sessionmaker() as db:
        await seed(db)
        service = NumberedCorpusImportService(db)
        await service.apply(
            persona_name="刁民",
            target_version="0.6.0",
            data="一\n\n二".encode(),
            expected_blocks=2,
        )
        with pytest.raises(AppError, match="does not match"):
            await service.preview(
                persona_name="刁民",
                target_version="0.6.0",
                data="一\n\n不同".encode(),
                expected_blocks=2,
            )
