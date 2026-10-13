from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import admin_required
from app.core.errors import AppError
from app.db.models import (
    DiscordConversation,
    Persona,
    PersonalityProfile,
    PersonaVersion,
    StyleProfile,
    Utterance,
)
from app.services.persona_update_service import CLONED_MODELS, PersonaUpdateService

VERSION_CHILD_MODELS = (
    *CLONED_MODELS,
    StyleProfile,
    PersonalityProfile,
)


@dataclass(frozen=True)
class ConsolidationReport:
    persona_id: UUID
    target_version_id: UUID
    target_version: str
    utterance_count: int
    verified_utterance_count: int
    repointed_conversation_count: int
    deleted_versions: tuple[str, ...]
    already_consolidated: bool = False


class PersonaVersionConsolidationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @admin_required
    async def consolidate(
        self,
        *,
        persona_name: str,
        source_version: str,
        target_version: str,
        delete_versions: set[str],
    ) -> ConsolidationReport:
        if target_version in delete_versions:
            raise AppError("invalid_version_plan", "Target version cannot be deleted.")
        if source_version not in delete_versions:
            raise AppError("invalid_version_plan", "Source version must be in the deletion set.")
        try:
            persona = await self.db.scalar(
                select(Persona).where(Persona.display_name == persona_name).limit(1)
            )
            if persona is None:
                raise AppError("persona_not_found", "Persona not found.")
            versions = list(
                await self.db.scalars(
                    select(PersonaVersion)
                    .where(PersonaVersion.persona_id == persona.id)
                    .order_by(PersonaVersion.created_at)
                )
            )
            by_label = {version.version: version for version in versions}
            existing_target = by_label.get(target_version)
            old_versions = [version for version in versions if version.version in delete_versions]
            if existing_target is not None:
                if old_versions:
                    raise AppError(
                        "partial_consolidation",
                        "Target and legacy versions coexist; manual inspection is required.",
                    )
                return await self._finalize_existing(persona, existing_target)
            missing = delete_versions - set(by_label)
            if missing:
                raise AppError(
                    "legacy_version_missing",
                    "Expected legacy versions are missing: " + ", ".join(sorted(missing)),
                )
            source = by_label[source_version]
            if source.status != "published":
                raise AppError("source_not_published", "Source version must be published.")

            target = PersonaVersion(
                persona_id=persona.id,
                version=target_version,
                status="published",
                change_notes=(
                    f"Consolidated from {source_version}; legacy versions removed after validation."
                ),
                published_at=datetime.now(UTC),
            )
            self.db.add(target)
            await self.db.flush()
            await PersonaUpdateService(self.db)._clone_data(source.id, target.id)
            await self.db.flush()

            source_count = await self._utterance_count(source.id)
            target_count = await self._utterance_count(target.id)
            verified_count = await self._utterance_count(target.id, verified=True)
            if target_count != source_count or verified_count == 0:
                raise AppError(
                    "clone_validation_failed",
                    "Target version did not reproduce the source utterance set.",
                )

            persona.active_version_id = target.id
            conversation_result = await self.db.execute(
                update(DiscordConversation)
                .where(DiscordConversation.persona_id == persona.id)
                .values(persona_version_id=target.id)
            )
            await self.db.flush()

            legacy_ids = [version.id for version in old_versions]
            for model in VERSION_CHILD_MODELS:
                await self.db.execute(delete(model).where(model.persona_version_id.in_(legacy_ids)))
            await self.db.execute(delete(PersonaVersion).where(PersonaVersion.id.in_(legacy_ids)))
            await self.db.commit()
            return ConsolidationReport(
                persona_id=persona.id,
                target_version_id=target.id,
                target_version=target.version,
                utterance_count=target_count,
                verified_utterance_count=verified_count,
                repointed_conversation_count=conversation_result.rowcount or 0,
                deleted_versions=tuple(sorted(delete_versions)),
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

    async def _finalize_existing(
        self, persona: Persona, target: PersonaVersion
    ) -> ConsolidationReport:
        if target.status != "published":
            raise AppError("target_not_published", "Existing target version is not published.")
        persona.active_version_id = target.id
        result = await self.db.execute(
            update(DiscordConversation)
            .where(DiscordConversation.persona_id == persona.id)
            .values(persona_version_id=target.id)
        )
        utterance_count = await self._utterance_count(target.id)
        verified_count = await self._utterance_count(target.id, verified=True)
        if verified_count == 0:
            raise AppError("target_empty", "Existing target version has no verified utterances.")
        await self.db.commit()
        return ConsolidationReport(
            persona_id=persona.id,
            target_version_id=target.id,
            target_version=target.version,
            utterance_count=utterance_count,
            verified_utterance_count=verified_count,
            repointed_conversation_count=result.rowcount or 0,
            deleted_versions=(),
            already_consolidated=True,
        )
