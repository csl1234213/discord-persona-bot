import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import admin_required
from app.core.errors import AppError
from app.db.models import DiscordConversation, Persona, PersonaVersion, Utterance
from app.services.persona_update_service import PersonaUpdateService

VOICE_PAIR_REFERENCE_PREFIX = "voice_pair:"


@dataclass(frozen=True)
class NumberedCorpusPreview:
    persona_id: UUID
    source_version_id: UUID
    source_version: str
    target_version: str
    source_utterance_count: int
    block_count: int
    unique_text_count: int
    duplicate_text_block_groups: tuple[tuple[int, ...], ...]
    source_sha256: str
    target_exists: bool
    already_applied: bool
    planned_database_changes: int


@dataclass(frozen=True)
class NumberedCorpusImportReport:
    persona_id: UUID
    source_version_id: UUID
    source_version: str
    target_version_id: UUID
    target_version: str
    target_utterance_count: int
    target_verified_utterance_count: int
    numbered_reference_count: int
    repointed_conversation_count: int
    source_sha256: str
    already_applied: bool = False


class NumberedCorpusImportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def parse_blocks(data: bytes) -> tuple[list[str], str]:
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("invalid_encoding", "Corpus file must be valid UTF-8.") from exc
        text = decoded.replace("\r\n", "\n").replace("\r", "\n")
        blocks = [
            unicodedata.normalize("NFC", block.strip())
            for block in re.split(r"\n[ \t]*\n+", text)
            if block.strip()
        ]
        if not blocks:
            raise AppError("empty_corpus", "Corpus file contains no text blocks.")
        return blocks, hashlib.sha256(data).hexdigest()

    async def preview(
        self,
        *,
        persona_name: str,
        target_version: str,
        data: bytes,
        expected_blocks: int | None = None,
    ) -> NumberedCorpusPreview:
        blocks, source_sha256 = self.parse_blocks(data)
        if expected_blocks is not None and len(blocks) != expected_blocks:
            raise AppError(
                "unexpected_block_count",
                f"Expected {expected_blocks} text blocks, found {len(blocks)}.",
            )
        persona = await self.db.scalar(
            select(Persona).where(Persona.display_name == persona_name).limit(1)
        )
        if persona is None or persona.active_version_id is None:
            raise AppError("persona_not_found", "Published persona not found.")
        source = await self.db.get(PersonaVersion, persona.active_version_id)
        if source is None or source.status != "published":
            raise AppError("source_not_published", "Active persona version is not published.")
        source_count = await self._utterance_count(source.id)
        existing = await self.db.scalar(
            select(PersonaVersion)
            .where(
                PersonaVersion.persona_id == persona.id,
                PersonaVersion.version == target_version,
            )
            .limit(1)
        )
        already_applied = False
        if existing is not None:
            already_applied = await self._matches_numbered_corpus(existing, blocks)
            if not already_applied:
                raise AppError(
                    "version_conflict",
                    "Target version exists but does not match the numbered corpus.",
                )
        positions: dict[str, list[int]] = defaultdict(list)
        for index, block in enumerate(blocks, start=1):
            positions[block].append(index)
        duplicate_groups = tuple(
            tuple(indices) for indices in positions.values() if len(indices) > 1
        )
        return NumberedCorpusPreview(
            persona_id=persona.id,
            source_version_id=source.id,
            source_version=source.version,
            target_version=target_version,
            source_utterance_count=source_count,
            block_count=len(blocks),
            unique_text_count=len(set(blocks)),
            duplicate_text_block_groups=duplicate_groups,
            source_sha256=source_sha256,
            target_exists=existing is not None,
            already_applied=already_applied,
            planned_database_changes=0 if already_applied else len(blocks) + 1,
        )

    @admin_required
    async def apply(
        self,
        *,
        persona_name: str,
        target_version: str,
        data: bytes,
        expected_blocks: int | None = None,
    ) -> NumberedCorpusImportReport:
        try:
            preview = await self.preview(
                persona_name=persona_name,
                target_version=target_version,
                data=data,
                expected_blocks=expected_blocks,
            )
            blocks, source_sha256 = self.parse_blocks(data)
            if preview.already_applied:
                target = await self.db.scalar(
                    select(PersonaVersion).where(
                        PersonaVersion.persona_id == preview.persona_id,
                        PersonaVersion.version == target_version,
                    )
                )
                if target is None:
                    raise AppError("version_missing", "Target version disappeared.")
                return await self._existing_report(preview, target)

            persona = await self.db.get(Persona, preview.persona_id)
            source = await self.db.get(PersonaVersion, preview.source_version_id)
            if persona is None or source is None:
                raise AppError("source_missing", "Persona source version disappeared.")
            target = PersonaVersion(
                persona_id=persona.id,
                version=target_version,
                status="published",
                change_notes=(
                    f"Replaced corpus with {len(blocks)} numbered text blocks; "
                    f"source SHA-256 {source_sha256}."
                ),
                published_at=datetime.now(UTC),
            )
            self.db.add(target)
            await self.db.flush()
            await PersonaUpdateService(self.db)._clone_data(source.id, target.id)
            await self.db.flush()
            await self.db.execute(
                delete(Utterance).where(Utterance.persona_version_id == target.id)
            )
            self.db.add_all(
                [
                    Utterance(
                        persona_version_id=target.id,
                        text=block,
                        source_type="user_provided",
                        source_reference=self.reference_for(index),
                        verified=True,
                    )
                    for index, block in enumerate(blocks, start=1)
                ]
            )
            await self.db.flush()

            target_count = await self._utterance_count(target.id)
            verified_count = await self._utterance_count(target.id, verified=True)
            references = list(
                await self.db.scalars(
                    select(Utterance.source_reference)
                    .where(Utterance.persona_version_id == target.id)
                    .order_by(Utterance.source_reference)
                )
            )
            expected_references = [self.reference_for(index) for index in range(1, len(blocks) + 1)]
            if (
                target_count != len(blocks)
                or verified_count != len(blocks)
                or references != expected_references
                or len(set(references)) != len(references)
                or await self._utterance_count(source.id) != preview.source_utterance_count
            ):
                raise AppError(
                    "numbered_corpus_validation_failed",
                    "Target corpus failed numbered import validation.",
                )

            persona.active_version_id = target.id
            conversation_result = await self.db.execute(
                update(DiscordConversation)
                .where(DiscordConversation.persona_id == persona.id)
                .values(persona_version_id=target.id)
            )
            await self.db.commit()
            return NumberedCorpusImportReport(
                persona_id=persona.id,
                source_version_id=source.id,
                source_version=source.version,
                target_version_id=target.id,
                target_version=target.version,
                target_utterance_count=target_count,
                target_verified_utterance_count=verified_count,
                numbered_reference_count=len(references),
                repointed_conversation_count=conversation_result.rowcount or 0,
                source_sha256=source_sha256,
            )
        except Exception:
            await self.db.rollback()
            raise

    @staticmethod
    def reference_for(index: int) -> str:
        return f"{VOICE_PAIR_REFERENCE_PREFIX}{index:03d}"

    async def _matches_numbered_corpus(self, version: PersonaVersion, blocks: list[str]) -> bool:
        if version.status != "published":
            return False
        rows = list(
            await self.db.scalars(
                select(Utterance)
                .where(Utterance.persona_version_id == version.id)
                .order_by(Utterance.source_reference)
            )
        )
        if len(rows) != len(blocks) or not all(row.verified for row in rows):
            return False
        return all(
            row.source_reference == self.reference_for(index) and row.text == block
            for index, (row, block) in enumerate(zip(rows, blocks, strict=True), start=1)
        )

    async def _existing_report(
        self, preview: NumberedCorpusPreview, target: PersonaVersion
    ) -> NumberedCorpusImportReport:
        count = await self._utterance_count(target.id)
        verified = await self._utterance_count(target.id, verified=True)
        return NumberedCorpusImportReport(
            persona_id=preview.persona_id,
            source_version_id=preview.source_version_id,
            source_version=preview.source_version,
            target_version_id=target.id,
            target_version=target.version,
            target_utterance_count=count,
            target_verified_utterance_count=verified,
            numbered_reference_count=count,
            repointed_conversation_count=0,
            source_sha256=preview.source_sha256,
            already_applied=True,
        )

    async def _utterance_count(self, version_id: UUID, *, verified: bool | None = None) -> int:
        query = (
            select(func.count())
            .select_from(Utterance)
            .where(Utterance.persona_version_id == version_id)
        )
        if verified is not None:
            query = query.where(Utterance.verified.is_(verified))
        return int(await self.db.scalar(query) or 0)
