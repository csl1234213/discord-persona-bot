from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Utterance


class UtteranceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, utterance: Utterance) -> Utterance:
        self.db.add(utterance)
        await self.db.flush()
        return utterance

    async def get(self, utterance_id: UUID) -> Utterance | None:
        return await self.db.get(Utterance, utterance_id)

    async def list_for_version(
        self, version_id: UUID, *, verified: bool | None = None
    ) -> list[Utterance]:
        query = select(Utterance).where(Utterance.persona_version_id == version_id)
        if verified is not None:
            query = query.where(Utterance.verified == verified)
        query = query.order_by(Utterance.created_at, Utterance.id)
        return list(await self.db.scalars(query))

    async def delete(self, utterance: Utterance) -> None:
        await self.db.delete(utterance)
        await self.db.flush()
