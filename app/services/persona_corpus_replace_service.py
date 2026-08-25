from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import DiscordConversation, Persona, PersonaVersion, Utterance
from app.services.persona_update_service import PersonaUpdateService


@dataclass(frozen=True)
class CorpusReplaceReport:
    persona_id: UUID
    source_version_id: UUID
    source_version: str
    target_version_id: UUID
    target_version: str
    replaced_utterance_count: int
    target_utterance_count: int
    target_verified_utterance_count: int
    repointed_conversation_count: int


class PersonaCorpusReplaceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def replace_utterance_containing(
        self,
        *,
        persona_name: str,
        target_version: str,
        remove_fragment: str,
        replacement_text: str,
    ) -> CorpusReplaceReport:
        remove_fragment = remove_fragment.strip()
        replacement_text = replacement_text.strip()
        if not remove_fragment or not replacement_text:
            raise AppError("invalid_replacement", "Replacement values cannot be empty.")
        try:
            persona = await self.db.scalar(
                select(Persona).where(Persona.display_name == persona_name).limit(1)
            )
            if persona is None or persona.active_version_id is None:
                raise AppError("persona_not_found", "Published persona not found.")
            source = await self.db.get(PersonaVersion, persona.active_version_id)
            if source is None or source.status != "published":
                raise AppError("source_not_published", "Active persona version is not published.")
            if await self.db.scalar(
                select(func.count())
                .select_from(PersonaVersion)
                .where(
                    PersonaVersion.persona_id == persona.id,
                    PersonaVersion.version == target_version,
                )
            ):
                raise AppError("version_exists", "Target persona version already exists.")

            source_count = await self._utterance_count(source.id)
            source_verified_count = await self._utterance_count(source.id, verified=True)
            source_matches = list(
                await self.db.scalars(
                    select(Utterance)
                    .where(
                        Utterance.persona_version_id == source.id,
                        Utterance.text.contains(remove_fragment),
                    )
                    .order_by(Utterance.created_at, Utterance.id)
                )
            )
            if not source_matches:
                raise AppError(
                    "replacement_match_count",
                    "Expected at least one source match, found 0.",
                )
            duplicate_replacement = await self.db.scalar(
                select(func.count())
                .select_from(Utterance)
                .where(
                    Utterance.persona_version_id == source.id,
                    Utterance.text == replacement_text,
                )
            )
            if duplicate_replacement:
                raise AppError(
                    "replacement_duplicate",
                    "Replacement text already exists in the active version.",
                )

            target = PersonaVersion(
                persona_id=persona.id,
                version=target_version,
                status="published",
                change_notes=(
                    f"Cloned {source.version} unchanged except for one explicitly requested "
                    "utterance replacement."
                ),
                published_at=datetime.now(UTC),
            )
            self.db.add(target)
            await self.db.flush()
            await PersonaUpdateService(self.db)._clone_data(source.id, target.id)
            await self.db.flush()
            target_matches = list(
                await self.db.scalars(
                    select(Utterance)
                    .where(
                        Utterance.persona_version_id == target.id,
                        Utterance.text.contains(remove_fragment),
                    )
                    .order_by(Utterance.created_at, Utterance.id)
                )
            )
            if len(target_matches) != len(source_matches):
                raise AppError(
                    "clone_match_count",
                    "Cloned version did not preserve every replacement target.",
                )
            target_matches[0].text = replacement_text
            if len(target_matches) > 1:
                await self.db.execute(
                    delete(Utterance)
                    .where(Utterance.id.in_([item.id for item in target_matches[1:]]))
                    .execution_options(synchronize_session=False)
                )
            await self.db.flush()

            target_count = await self._utterance_count(target.id)
            target_verified_count = await self._utterance_count(target.id, verified=True)
            removed_verified_count = sum(item.verified for item in target_matches[1:])
            expected_target_count = source_count - len(source_matches) + 1
            expected_target_verified_count = source_verified_count - removed_verified_count
            remaining_fragment_count = await self.db.scalar(
                select(func.count())
                .select_from(Utterance)
                .where(
                    Utterance.persona_version_id == target.id,
                    Utterance.text.contains(remove_fragment),
                )
            )
            replacement_count = await self.db.scalar(
                select(func.count())
                .select_from(Utterance)
                .where(
                    Utterance.persona_version_id == target.id,
                    Utterance.text == replacement_text,
                )
            )
            if (
                target_count != expected_target_count
                or target_verified_count != expected_target_verified_count
                or remaining_fragment_count != 0
                or replacement_count != 1
                or await self._utterance_count(source.id) != source_count
            ):
                raise AppError(
                    "replacement_validation_failed",
                    "Target version failed replacement integrity validation.",
                )

            persona.active_version_id = target.id
            conversation_result = await self.db.execute(
                update(DiscordConversation)
                .where(DiscordConversation.persona_id == persona.id)
                .values(persona_version_id=target.id)
            )
            await self.db.commit()
            return CorpusReplaceReport(
                persona_id=persona.id,
                source_version_id=source.id,
                source_version=source.version,
                target_version_id=target.id,
                target_version=target.version,
                replaced_utterance_count=len(source_matches),
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
