import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Utterance, UtteranceVoiceAsset
from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.numbered_corpus_import_service import NumberedCorpusImportService
from app.services.persona_update_service import PersonaUpdateService
from app.services.voice_asset_import_service import VoiceAssetImportService


def corpus_bytes(*blocks: str) -> bytes:
    return "\n\n".join(blocks).encode("utf-8")


def write_mp3(directory: Path, index: int, marker: bytes | None = None) -> Path:
    path = directory / f"{index:03d}.mp3"
    path.write_bytes(b"ID3" + (marker or str(index).encode("ascii")))
    return path


async def seed_numbered(db, blocks: tuple[str, ...]):
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
    report = await NumberedCorpusImportService(db).apply(
        persona_name="刁民",
        target_version="0.6.0",
        data=corpus_bytes(*blocks),
        expected_blocks=len(blocks),
    )
    return persona, report


def settings(root: Path) -> Settings:
    return Settings(
        discord_sync_commands_on_startup=False,
        voice_asset_root=root,
    )


@pytest.mark.asyncio
async def test_complete_70_pair_mapping_logic_uses_synthetic_audio(db_sessionmaker, tmp_path):
    blocks = tuple(f"第 {index:03d} 条" for index in range(1, 71))
    for index in range(1, 71):
        write_mp3(tmp_path, index)
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=70,
        )
        assert preview.gate_passed
        assert preview.corpus_count == 70
        assert preview.text_pair_count == 70
        assert preview.audio_pair_count == 70
        assert preview.exact_mapped_count == 70
        assert len(preview.plans) == 70


@pytest.mark.asyncio
async def test_dry_run_is_read_only_then_apply_is_idempotent(db_sessionmaker, tmp_path):
    blocks = ("第一条", "第二条", "第三条")
    for index in range(1, 4):
        write_mp3(tmp_path, index)
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        service = VoiceAssetImportService(db, settings(tmp_path))
        preview = await service.preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=3,
        )
        count = await db.scalar(select(func.count()).select_from(UtteranceVoiceAsset))
        assert preview.gate_passed
        assert preview.corpus_count == 3
        assert preview.text_pair_count == 3
        assert preview.audio_pair_count == 3
        assert preview.complete_pair_count == 3
        assert preview.exact_mapped_count == 3
        assert preview.planned_create_count == 3
        assert count == 0

        report = await service.apply(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=3,
        )
        repeated = await service.apply(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=3,
        )
        assets = list(
            await db.scalars(
                select(UtteranceVoiceAsset).order_by(UtteranceVoiceAsset.audio_relative_path)
            )
        )
        assert report.created_count == 3
        assert report.total_asset_count == 3
        assert repeated.created_count == 0
        assert repeated.skipped_unchanged_count == 3
        assert [asset.audio_relative_path for asset in assets] == [
            "001.mp3",
            "002.mp3",
            "003.mp3",
        ]


@pytest.mark.asyncio
async def test_duplicate_text_is_disambiguated_by_numbered_reference(db_sessionmaker, tmp_path):
    blocks = ("相同", "中间", "相同")
    for index in range(1, 4):
        write_mp3(tmp_path, index)
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=3,
        )
        assert preview.gate_passed
        assert preview.duplicate_count == 1
        assert [plan.pair_index for plan in preview.plans] == [1, 2, 3]
        assert len({plan.utterance_id for plan in preview.plans}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio_indexes", "expected_conflict"),
    [
        ((1,), "audio_pair_count:1:expected:2"),
        ((1, 3), "unmatched_pair:002"),
    ],
)
async def test_missing_or_misnumbered_audio_fails_gate(
    db_sessionmaker, tmp_path, audio_indexes, expected_conflict
):
    blocks = ("第一条", "第二条")
    for index in audio_indexes:
        write_mp3(tmp_path, index)
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=2,
        )
        assert not preview.gate_passed
        assert expected_conflict in preview.conflicts


@pytest.mark.asyncio
async def test_missing_text_pair_fails_gate(db_sessionmaker, tmp_path):
    database_blocks = ("第一条", "第二条")
    write_mp3(tmp_path, 1)
    write_mp3(tmp_path, 2)
    async with db_sessionmaker() as db:
        await seed_numbered(db, database_blocks)
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes("第一条"),
            expected_pairs=2,
        )
        assert not preview.gate_passed
        assert "text_pair_count:1:expected:2" in preview.conflicts


@pytest.mark.asyncio
async def test_text_mismatch_and_ambiguous_reference_fail_gate(db_sessionmaker, tmp_path):
    blocks = ("第一条", "第二条")
    write_mp3(tmp_path, 1)
    write_mp3(tmp_path, 2)
    async with db_sessionmaker() as db:
        _, report = await seed_numbered(db, blocks)
        first = await db.scalar(
            select(Utterance).where(
                Utterance.persona_version_id == report.target_version_id,
                Utterance.source_reference == "voice_pair:001",
            )
        )
        assert first is not None
        first.text = "已改变"
        db.add(
            Utterance(
                persona_version_id=report.target_version_id,
                text="第二条",
                source_reference="voice_pair:002",
                verified=True,
            )
        )
        await db.commit()
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=2,
        )
        assert not preview.gate_passed
        assert "text_mismatch:001" in preview.conflicts
        assert "ambiguous_utterance:002" in preview.conflicts


@pytest.mark.asyncio
async def test_duplicate_audio_hash_zero_byte_and_bad_signature_fail_gate(
    db_sessionmaker, tmp_path
):
    blocks = ("第一条", "第二条", "第三条", "第四条")
    write_mp3(tmp_path, 1, b"same")
    write_mp3(tmp_path, 2, b"same")
    (tmp_path / "003.mp3").write_bytes(b"")
    (tmp_path / "004.mp3").write_bytes(b"not-an-mp3")
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        preview = await VoiceAssetImportService(db, settings(tmp_path)).preview(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=4,
        )
        assert not preview.gate_passed
        assert "duplicate_audio_groups:1" in preview.conflicts
        assert "empty_audio:003" in preview.conflicts
        assert "invalid_audio_signature:004" in preview.conflicts


@pytest.mark.asyncio
async def test_apply_rejects_conflicts_without_partial_writes(db_sessionmaker, tmp_path):
    blocks = ("第一条", "第二条")
    write_mp3(tmp_path, 1)
    async with db_sessionmaker() as db:
        await seed_numbered(db, blocks)
        service = VoiceAssetImportService(db, settings(tmp_path))
        with pytest.raises(AppError) as error:
            await service.apply(
                persona_name="刁民",
                source_dir=tmp_path,
                text_data=corpus_bytes(*blocks),
                expected_pairs=2,
            )
        count = await db.scalar(select(func.count()).select_from(UtteranceVoiceAsset))
        assert error.value.code == "voice_asset_import_gate_failed"
        assert count == 0


@pytest.mark.asyncio
async def test_repository_detects_text_audio_and_path_tampering(db_sessionmaker, tmp_path):
    blocks = ("第一条",)
    audio = write_mp3(tmp_path, 1, b"aaaa")
    async with db_sessionmaker() as db:
        _, report = await seed_numbered(db, blocks)
        service = VoiceAssetImportService(db, settings(tmp_path))
        await service.apply(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=1,
        )
        utterance = await db.scalar(
            select(Utterance).where(Utterance.persona_version_id == report.target_version_id)
        )
        assert utterance is not None
        repository = UtteranceVoiceAssetRepository(db, settings(tmp_path))
        assert (await repository.resolve_audio_path(utterance.id)).valid

        utterance.text = "篡改文字"
        await db.flush()
        assert (await repository.resolve_audio_path(utterance.id)).reason == "text_hash_mismatch"
        utterance.text = blocks[0]
        audio.write_bytes(b"ID3bbbb")
        await db.flush()
        assert (await repository.resolve_audio_path(utterance.id)).reason == "audio_hash_mismatch"

        asset = await repository.get_by_utterance_id(utterance.id)
        assert asset is not None
        asset.audio_relative_path = "../outside.mp3"
        await db.flush()
        assert (await repository.validate_asset(asset)).reason == "invalid_relative_path"


@pytest.mark.asyncio
async def test_repository_reports_missing_root_file_and_size_mismatch(db_sessionmaker, tmp_path):
    blocks = ("第一条",)
    audio = write_mp3(tmp_path, 1, b"payload")
    async with db_sessionmaker() as db:
        _, report = await seed_numbered(db, blocks)
        service = VoiceAssetImportService(db, settings(tmp_path))
        await service.apply(
            persona_name="刁民",
            source_dir=tmp_path,
            text_data=corpus_bytes(*blocks),
            expected_pairs=1,
        )
        utterance = await db.scalar(
            select(Utterance).where(Utterance.persona_version_id == report.target_version_id)
        )
        assert utterance is not None
        no_root = UtteranceVoiceAssetRepository(
            db, Settings(discord_sync_commands_on_startup=False)
        )
        assert (await no_root.resolve_audio_path(utterance.id)).reason == "root_not_configured"
        audio.unlink()
        repository = UtteranceVoiceAssetRepository(db, settings(tmp_path))
        assert (await repository.resolve_audio_path(utterance.id)).reason == "file_missing"
        audio.write_bytes(b"ID3payload-extra")
        assert (await repository.resolve_audio_path(utterance.id)).reason == "audio_size_mismatch"


def test_sha256_metadata_uses_exact_text_and_audio_bytes(tmp_path):
    audio = write_mp3(tmp_path, 1, b"payload")
    assert (
        VoiceAssetImportService._sha256_text("粤语") == hashlib.sha256("粤语".encode()).hexdigest()
    )
    assert (
        VoiceAssetImportService._sha256_file(audio)
        == hashlib.sha256(audio.read_bytes()).hexdigest()
    )
