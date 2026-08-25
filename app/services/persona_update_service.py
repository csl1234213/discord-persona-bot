from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    AddressRule,
    CatchphraseRule,
    Persona,
    PersonalityProfile,
    PersonaVersion,
    StyleProfile,
    Utterance,
    VocabularyRule,
)
from app.repositories.persona_repository import PersonaRepository
from app.repositories.utterance_repository import UtteranceRepository
from app.repositories.version_repository import VersionRepository
from app.schemas.persona import PersonaCreate, UtteranceCreate, VersionCreate

CLONED_MODELS = (Utterance, VocabularyRule, CatchphraseRule, AddressRule)


class PersonaUpdateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.personas = PersonaRepository(db)
        self.versions = VersionRepository(db)
        self.utterances = UtteranceRepository(db)

    async def create_persona(self, payload: PersonaCreate) -> tuple[Persona, PersonaVersion]:
        persona = await self.personas.add(Persona(**payload.model_dump()))
        version = await self.versions.add(
            PersonaVersion(
                persona_id=persona.id,
                version="0.1.0",
                status="draft",
                change_notes="Initial draft",
            )
        )
        self.db.add_all(
            [
                StyleProfile(persona_version_id=version.id),
                PersonalityProfile(persona_version_id=version.id),
            ]
        )
        await self.db.commit()
        return persona, version

    async def get_persona(self, persona_id: UUID) -> Persona:
        persona = await self.personas.get(persona_id)
        if persona is None:
            raise AppError("persona_not_found", "Persona not found.")
        return persona

    async def get_version(self, persona_id: UUID, version_id: UUID) -> PersonaVersion:
        version = await self.versions.get(version_id)
        if version is None or version.persona_id != persona_id:
            raise AppError("version_not_found", "Persona version not found.")
        return version

    async def require_draft(self, persona_id: UUID, version_id: UUID) -> PersonaVersion:
        version = await self.get_version(persona_id, version_id)
        if version.status != "draft":
            raise AppError("version_immutable", "Published versions are immutable.")
        return version

    async def create_draft(self, persona_id: UUID, payload: VersionCreate) -> PersonaVersion:
        persona = await self.get_persona(persona_id)
        versions = await self.versions.list_for_persona(persona_id)
        if any(item.version == payload.version for item in versions):
            raise AppError("version_exists", "Version label already exists.")
        source_id = payload.clone_from_version_id or persona.active_version_id
        source = await self.get_version(persona_id, source_id) if source_id else None
        target = await self.versions.add(
            PersonaVersion(
                persona_id=persona_id,
                version=payload.version,
                change_notes=payload.change_notes,
            )
        )
        if source:
            await self._clone_data(source.id, target.id)
        else:
            self.db.add_all(
                [
                    StyleProfile(persona_version_id=target.id),
                    PersonalityProfile(persona_version_id=target.id),
                ]
            )
        await self.db.commit()
        return target

    async def _clone_data(self, source_id: UUID, target_id: UUID) -> None:
        for model in CLONED_MODELS:
            rows = await self.db.scalars(select(model).where(model.persona_version_id == source_id))
            for row in rows:
                values = {
                    column.name: deepcopy(getattr(row, column.name))
                    for column in model.__table__.columns
                    if column.name not in {"id", "persona_version_id", "created_at"}
                }
                self.db.add(model(persona_version_id=target_id, **values))
        for model in (StyleProfile, PersonalityProfile):
            row = await self.db.get(model, source_id)
            values = (
                {
                    column.name: deepcopy(getattr(row, column.name))
                    for column in model.__table__.columns
                    if column.name != "persona_version_id"
                }
                if row
                else {}
            )
            self.db.add(model(persona_version_id=target_id, **values))

    async def add_utterance(
        self, persona_id: UUID, version_id: UUID, payload: UtteranceCreate
    ) -> Utterance:
        await self.require_draft(persona_id, version_id)
        utterance = await self.utterances.add(
            Utterance(persona_version_id=version_id, **payload.model_dump())
        )
        await self.db.commit()
        return utterance

    async def set_verified(
        self, persona_id: UUID, version_id: UUID, utterance_id: UUID, verified: bool
    ) -> Utterance:
        await self.require_draft(persona_id, version_id)
        utterance = await self.utterances.get(utterance_id)
        if utterance is None or utterance.persona_version_id != version_id:
            raise AppError("utterance_not_found", "Utterance not found.")
        utterance.verified = verified
        await self.db.commit()
        return utterance

    async def delete_utterance(
        self, persona_id: UUID, version_id: UUID, utterance_id: UUID
    ) -> None:
        await self.require_draft(persona_id, version_id)
        utterance = await self.utterances.get(utterance_id)
        if utterance is None or utterance.persona_version_id != version_id:
            raise AppError("utterance_not_found", "Utterance not found.")
        await self.utterances.delete(utterance)
        await self.db.commit()

    async def publish(self, persona_id: UUID, version_id: UUID) -> PersonaVersion:
        persona = await self.get_persona(persona_id)
        version = await self.require_draft(persona_id, version_id)
        verified = await self.utterances.list_for_version(version_id, verified=True)
        if not verified:
            raise AppError(
                "publish_validation_failed", "At least one verified utterance is required."
            )
        version.status = "published"
        version.published_at = datetime.now(UTC)
        persona.active_version_id = version.id
        await self.db.commit()
        return version

    async def activate(self, persona_id: UUID, version_id: UUID) -> PersonaVersion:
        persona = await self.get_persona(persona_id)
        version = await self.get_version(persona_id, version_id)
        if version.status != "published":
            raise AppError("version_not_published", "Only published versions can be activated.")
        persona.active_version_id = version.id
        await self.db.commit()
        return version

    async def rollback(self, persona_id: UUID) -> PersonaVersion:
        persona = await self.get_persona(persona_id)
        published = [
            item
            for item in await self.versions.list_for_persona(persona_id)
            if item.status == "published" and item.id != persona.active_version_id
        ]
        if not published:
            raise AppError("rollback_unavailable", "No previous published version is available.")
        previous = max(published, key=lambda item: item.published_at or item.created_at)
        persona.active_version_id = previous.id
        await self.db.commit()
        return previous
