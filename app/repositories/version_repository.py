from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PersonaVersion


class VersionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, version: PersonaVersion) -> PersonaVersion:
        self.db.add(version)
        await self.db.flush()
        return version

    async def get(self, version_id: UUID) -> PersonaVersion | None:
        return await self.db.get(PersonaVersion, version_id)

    async def list_for_persona(self, persona_id: UUID) -> list[PersonaVersion]:
        query = (
            select(PersonaVersion)
            .where(PersonaVersion.persona_id == persona_id)
            .order_by(PersonaVersion.created_at)
        )
        return list(await self.db.scalars(query))
