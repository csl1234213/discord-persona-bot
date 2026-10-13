"""Scoped chat erasure and bounded retention; never touches corpus/voice assets."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text

from app.core.authorization import require_actor, require_admin
from app.core.errors import AppError
from app.db.models import DiscordConversation, DiscordMessageRecord


class PrivacyService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    async def _delete_conversations(self, query) -> int:
        ids = select(DiscordConversation.id).where(query)
        await self.db.execute(
            delete(DiscordMessageRecord).where(DiscordMessageRecord.conversation_id.in_(ids))
        )
        result = await self.db.execute(delete(DiscordConversation).where(query))
        return result.rowcount

    async def erase(self, context) -> int:
        require_actor(self.db, context)
        if (
            context.guild_id != self.settings.discord_guild_id
            or context.channel_id != self.settings.discord_chat_channel_id
            or context.thread_id is not None
        ):
            raise AppError("forbidden", "Erasure scope is not authorized.")
        from sqlalchemy import and_

        await self.db.execute(text("PRAGMA secure_delete=ON"))
        count = await self._delete_conversations(
            and_(
                DiscordConversation.guild_id == context.guild_id,
                DiscordConversation.channel_id == context.channel_id,
                DiscordConversation.discord_user_id == context.discord_user_id,
            )
        )
        await self.db.commit()
        # A busy checkpoint fails explicitly; never claim backup/SSD forensic erasure.
        checkpoint = (await self.db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))).first()
        await self.db.commit()
        if checkpoint and checkpoint[0]:
            raise AppError("erasure_checkpoint_busy", "Deletion committed; checkpoint pending.")
        return count

    async def cleanup(self) -> int:
        """Trusted internal maintenance, not exposed to Discord callers."""
        require_admin(self.db)
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.security_retention_days)
        await self.db.execute(text("PRAGMA secure_delete=ON"))
        removed = await self._delete_conversations(DiscordConversation.last_active_at < cutoff)
        await self.db.execute(
            delete(DiscordMessageRecord).where(DiscordMessageRecord.created_at < cutoff)
        )
        stale = (
            select(DiscordConversation.id)
            .order_by(DiscordConversation.last_active_at.desc(), DiscordConversation.id.desc())
            .offset(self.settings.security_max_conversations)
        )
        await self.db.execute(
            delete(DiscordMessageRecord).where(DiscordMessageRecord.conversation_id.in_(stale))
        )
        await self.db.execute(delete(DiscordConversation).where(DiscordConversation.id.in_(stale)))
        message_ids = (
            select(DiscordMessageRecord.id)
            .order_by(DiscordMessageRecord.created_at.desc(), DiscordMessageRecord.id.desc())
            .offset(self.settings.security_max_messages)
        )
        await self.db.execute(
            delete(DiscordMessageRecord).where(DiscordMessageRecord.id.in_(message_ids))
        )
        await self.db.execute(
            text("DELETE FROM security_usage WHERE day < :day"),
            {"day": (datetime.now(UTC) - timedelta(days=2)).date().isoformat()},
        )
        await self.db.execute(
            text("DELETE FROM admin_interactions WHERE expires < :now"), {"now": datetime.now(UTC)}
        )
        await self.db.commit()
        return removed

    async def check_capacity(self):
        await self.db.execute(text("BEGIN IMMEDIATE"))
        conversations = await self.db.scalar(select(func.count()).select_from(DiscordConversation))
        messages = await self.db.scalar(select(func.count()).select_from(DiscordMessageRecord))
        if (
            conversations >= self.settings.security_max_conversations
            or messages + 2 > self.settings.security_max_messages
        ):
            raise AppError(
                "storage_capacity", "Chat storage capacity reached; retry after cleanup."
            )
