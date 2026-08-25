import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import DiscordConversation, PersonaVersion, Utterance
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.persona_corpus_replace_service import PersonaCorpusReplaceService
from app.services.persona_update_service import PersonaUpdateService

REMOVE_FRAGMENT = "[REMOVE-THIS-FRAGMENT]"
ORIGINAL_TEXT = f"授权测试文本前缀 {REMOVE_FRAGMENT} 后缀"
REPLACEMENT_TEXT = "授权测试文本已完成替换"


async def seed_v04(db):
    update_service = PersonaUpdateService(db)
    persona, version = await update_service.create_persona(
        PersonaCreate(display_name="刁民", coverage_threshold=0.05)
    )
    version.version = "0.4.0"
    await update_service.add_utterance(
        persona.id,
        version.id,
        UtteranceCreate(text="保留语料", verified=True),
    )
    await update_service.add_utterance(
        persona.id,
        version.id,
        UtteranceCreate(text=ORIGINAL_TEXT, verified=True),
    )
    await update_service.publish(persona.id, version.id)
    return persona, version


@pytest.mark.asyncio
async def test_replace_clones_source_changes_one_target_and_repoints_conversations(
    db_sessionmaker,
):
    async with db_sessionmaker() as db:
        persona, source = await seed_v04(db)
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

        report = await PersonaCorpusReplaceService(db).replace_utterance_containing(
            persona_name="刁民",
            target_version="0.5.0",
            remove_fragment=REMOVE_FRAGMENT,
            replacement_text=REPLACEMENT_TEXT,
        )

        versions = list(
            await db.scalars(
                select(PersonaVersion)
                .where(PersonaVersion.persona_id == persona.id)
                .order_by(PersonaVersion.version)
            )
        )
        assert [version.version for version in versions] == ["0.4.0", "0.5.0"]
        target = versions[1]
        await db.refresh(persona)
        assert persona.active_version_id == target.id
        assert report.replaced_utterance_count == 1
        assert report.target_utterance_count == 2
        assert report.target_verified_utterance_count == 2
        assert report.repointed_conversation_count == 1

        source_after = list(
            await db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == source.id)
                .order_by(Utterance.created_at, Utterance.id)
            )
        )
        assert [(item.id, item.text, item.verified) for item in source_after] == source_snapshot
        target_texts = set(
            await db.scalars(
                select(Utterance.text).where(Utterance.persona_version_id == target.id)
            )
        )
        assert target_texts == {"保留语料", REPLACEMENT_TEXT}
        assert all(REMOVE_FRAGMENT not in text for text in target_texts)
        conversation = await db.scalar(
            select(DiscordConversation).where(DiscordConversation.persona_id == persona.id)
        )
        assert conversation.persona_version_id == target.id


@pytest.mark.asyncio
async def test_missing_fragment_rolls_back_without_new_version(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, _ = await seed_v04(db)
        persona_id = persona.id
        with pytest.raises(AppError, match="Expected at least one source match"):
            await PersonaCorpusReplaceService(db).replace_utterance_containing(
                persona_name="刁民",
                target_version="0.5.0",
                remove_fragment="不存在的片段",
                replacement_text=REPLACEMENT_TEXT,
            )
        versions = list(
            await db.scalars(select(PersonaVersion).where(PersonaVersion.persona_id == persona_id))
        )
        assert [version.version for version in versions] == ["0.4.0"]


@pytest.mark.asyncio
async def test_multiple_matches_are_replaced_and_deduplicated(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, source = await seed_v04(db)
        duplicate_match = Utterance(
            persona_version_id=source.id,
            text=f"另一条{REMOVE_FRAGMENT}授权测试文本",
            verified=True,
        )
        db.add(duplicate_match)
        await db.commit()
        source_texts_before = list(
            await db.scalars(
                select(Utterance.text)
                .where(Utterance.persona_version_id == source.id)
                .order_by(Utterance.text)
            )
        )

        report = await PersonaCorpusReplaceService(db).replace_utterance_containing(
            persona_name="刁民",
            target_version="0.5.0",
            remove_fragment=REMOVE_FRAGMENT,
            replacement_text=REPLACEMENT_TEXT,
        )

        target = await db.scalar(
            select(PersonaVersion).where(
                PersonaVersion.persona_id == persona.id,
                PersonaVersion.version == "0.5.0",
            )
        )
        assert target is not None
        target_texts = list(
            await db.scalars(
                select(Utterance.text).where(Utterance.persona_version_id == target.id)
            )
        )
        source_texts_after = list(
            await db.scalars(
                select(Utterance.text)
                .where(Utterance.persona_version_id == source.id)
                .order_by(Utterance.text)
            )
        )
        assert report.replaced_utterance_count == 2
        assert report.target_utterance_count == 2
        assert report.target_verified_utterance_count == 2
        assert target_texts.count(REPLACEMENT_TEXT) == 1
        assert all(REMOVE_FRAGMENT not in text for text in target_texts)
        assert source_texts_after == source_texts_before
