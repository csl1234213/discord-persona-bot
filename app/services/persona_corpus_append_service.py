from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import admin_required
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import DiscordConversation, Persona, PersonaVersion, Utterance
from app.services.attachment_import_service import AttachmentImportService
from app.services.persona_update_service import PersonaUpdateService


@dataclass(frozen=True)
class CorpusAppendReport:
    persona_id: UUID
    source_version_id: UUID
    source_version: str
    target_version_id: UUID
    target_version: str
    source_utterance_count: int
    appended_utterance_count: int
    duplicate_count: int
    target_utterance_count: int
    target_verified_utterance_count: int
    repointed_conversation_count: int


class PersonaCorpusAppendService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @admin_required
    async def append_text_file(
        self,
        *,
        persona_name: str,
        target_version: str,
        filename: str,
        data: bytes,
    ) -> CorpusAppendReport:
        try:
            persona = await self.db.scalar(
                select(Persona).where(Persona.display_name == persona_name).limit(1)
            )
            if persona is None or persona.active_version_id is None:
                raise AppError("persona_not_found", "Published persona not found.")
            source = await self.db.get(PersonaVersion, persona.active_version_id)
            if source is None or source.status != "published":
                raise AppError("source_not_published", "Active persona version is not published.")
            version_exists = await self.db.scalar(
                select(func.count())
                .select_from(PersonaVersion)
                .where(
                    PersonaVersion.persona_id == persona.id,
                    PersonaVersion.version == target_version,
                )
            )
            if version_exists:
                raise AppError("version_exists", "Target persona version already exists.")

            preview = await AttachmentImportService(self.db, self.settings).preview(
                version_id=source.id,
                filename=filename,
                content_type="text/plain",
                data=data,
                verify=True,
            )
            source_count = await self._utterance_count(source.id)
            source_verified_count = await self._utterance_count(source.id, verified=True)

            target = PersonaVersion(
                persona_id=persona.id,
                version=target_version,
                status="published",
                change_notes=(
                    f"Cloned {source.version} unchanged and appended "
                    f"{len(preview.items)} authorized utterances from {filename}."
                ),
                published_at=datetime.now(UTC),
            )
            self.db.add(target)
            await self.db.flush()
            await PersonaUpdateService(self.db)._clone_data(source.id, target.id)
            for item in preview.items:
                payload = item.model_dump()
                payload["source_reference"] = payload["source_reference"] or filename
                self.db.add(Utterance(persona_version_id=target.id, **payload))
            await self.db.flush()

            target_count = await self._utterance_count(target.id)
            target_verified_count = await self._utterance_count(target.id, verified=True)
            appended_count = len(preview.items)
            if (
                target_count != source_count + appended_count
                or target_verified_count != source_verified_count + appended_count
                or await self._utterance_count(source.id) != source_count
            ):
                raise AppError(
                    "append_validation_failed",
                    "Target version did not preserve the source and append every new utterance.",
                )

            persona.active_version_id = target.id
            conversation_result = await self.db.execute(
                update(DiscordConversation)
                .where(DiscordConversation.persona_id == persona.id)
                .values(persona_version_id=target.id)
            )
            await self.db.commit()
            return CorpusAppendReport(
                persona_id=persona.id,
                source_version_id=source.id,
                source_version=source.version,
                target_version_id=target.id,
                target_version=target.version,
                source_utterance_count=source_count,
                appended_utterance_count=appended_count,
                duplicate_count=preview.duplicate_count,
                target_utterance_count=target_count,
                target_verified_utterance_count=target_verified_count,
                repointed_conversation_count=conversation_result.rowcount or 0,
            )
        except Exception:
            await self.db.rollback()
            raise

    async def _utterance_count(self, version_id: UUID, *, verified: bool | None = None) -> int:
        query = (
            select(func.count())
            .select_from(Utterance)
            .where(Utterance.persona_version_id == version_id)
        )
        if verified is not None:
            query = query.where(Utterance.verified.is_(verified))
        return int(await self.db.scalar(query) or 0)
