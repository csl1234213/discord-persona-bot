from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiscordConversation, DiscordMessageRecord


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_active(
        self,
        *,
        persona_id: UUID,
        persona_version_id: UUID,
        guild_id: str | None,
        channel_id: str,
        discord_user_id: str,
    ) -> DiscordConversation | None:
        query = (
            select(DiscordConversation)
            .where(
                DiscordConversation.persona_id == persona_id,
                DiscordConversation.persona_version_id == persona_version_id,
                DiscordConversation.guild_id == guild_id,
                DiscordConversation.channel_id == channel_id,
                DiscordConversation.discord_user_id == discord_user_id,
                DiscordConversation.is_active.is_(True),
            )
            .order_by(DiscordConversation.last_active_at.desc())
        )
        return (await self.db.scalars(query.limit(1))).first()

    async def add(self, conversation: DiscordConversation) -> DiscordConversation:
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def add_message(self, message: DiscordMessageRecord) -> DiscordMessageRecord:
        self.db.add(message)
        await self.db.flush()
        return message

    async def messages(self, conversation_id: UUID, *, limit: int) -> list[DiscordMessageRecord]:
        query = (
            select(DiscordMessageRecord)
            .where(DiscordMessageRecord.conversation_id == conversation_id)
            .order_by(DiscordMessageRecord.created_at.desc())
            .limit(limit)
        )
        return list(reversed(list(await self.db.scalars(query))))

    async def prune_messages(self, conversation_id: UUID, *, keep: int, cutoff: datetime) -> None:
        await self.db.execute(
            delete(DiscordMessageRecord)
            .where(
                DiscordMessageRecord.conversation_id == conversation_id,
                DiscordMessageRecord.created_at < cutoff,
            )
            .execution_options(synchronize_session=False)
        )
        stale_ids = list(
            await self.db.scalars(
                select(DiscordMessageRecord.id)
                .where(DiscordMessageRecord.conversation_id == conversation_id)
                .order_by(DiscordMessageRecord.created_at.desc(), DiscordMessageRecord.id.desc())
                .offset(keep)
            )
        )
        if stale_ids:
            await self.db.execute(
                delete(DiscordMessageRecord)
                .where(DiscordMessageRecord.id.in_(stale_ids))
                .execution_options(synchronize_session=False)
            )

    async def reset_for_user(
        self,
        *,
        persona_id: UUID,
        persona_version_id: UUID,
        guild_id: str | None,
        channel_id: str,
        discord_user_id: str,
    ) -> int:
        conversation = await self.find_active(
            persona_id=persona_id,
            persona_version_id=persona_version_id,
            guild_id=guild_id,
            channel_id=channel_id,
            discord_user_id=discord_user_id,
        )
        if conversation is None:
            return 0
        conversation.is_active = False
        conversation.last_active_at = datetime.now(UTC)
        await self.db.flush()
        return 1
