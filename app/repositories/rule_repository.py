from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AddressRule, CatchphraseRule, VocabularyRule


class RuleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def addresses(self, version_id: UUID) -> list[AddressRule]:
        return list(
            await self.db.scalars(
                select(AddressRule).where(AddressRule.persona_version_id == version_id)
            )
        )

    async def catchphrases(self, version_id: UUID) -> list[CatchphraseRule]:
        return list(
            await self.db.scalars(
                select(CatchphraseRule).where(CatchphraseRule.persona_version_id == version_id)
            )
        )

    async def vocabulary(self, version_id: UUID) -> list[VocabularyRule]:
        return list(
            await self.db.scalars(
                select(VocabularyRule).where(VocabularyRule.persona_version_id == version_id)
            )
        )
