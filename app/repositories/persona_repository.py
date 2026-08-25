from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Persona


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, persona: Persona) -> Persona:
        self.db.add(persona)
        await self.db.flush()
        return persona

    async def get(self, persona_id: UUID) -> Persona | None:
        return await self.db.get(Persona, persona_id)

    async def list(self) -> list[Persona]:
        result = await self.db.scalars(select(Persona).order_by(Persona.created_at))
        return list(result)
