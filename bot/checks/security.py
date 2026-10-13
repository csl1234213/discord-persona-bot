from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic

import discord
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.authorization import grant_local_admin
from app.core.errors import AppError


def require_origin(original, current):
    if (
        any(
            getattr(original, key, None) != getattr(current, key, None)
            for key in ("guild_id", "channel_id")
        )
        or original.user.id != current.user.id
    ):
        raise AppError("forbidden", "Interaction actor or scope mismatch.")
    created = getattr(original, "created_at", None)
    if isinstance(created, datetime) and (datetime.now(UTC) - created).total_seconds() > 900:
        raise AppError("expired_interaction", "Interaction has expired.")


def interaction_in_scope(interaction, settings) -> bool:
    return (
        settings.discord_guild_id is not None
        and settings.discord_chat_channel_id is not None
        and str(getattr(interaction, "guild_id", None)) == settings.discord_guild_id
        and str(getattr(interaction, "channel_id", None)) == settings.discord_chat_channel_id
        and not isinstance(getattr(interaction, "channel", None), discord.Thread)
    )


@asynccontextmanager
async def admin_session(bot, interaction):
    require_origin(interaction, interaction)
    settings = getattr(bot, "settings", None) or interaction.client.settings
    if (
        not interaction_in_scope(interaction, settings)
        or str(interaction.user.id) not in settings.discord_owner_user_ids
    ):
        raise AppError("forbidden", "Administrator authority required.")
    async with bot.db_sessionmaker() as db:
        event_id = getattr(interaction, "id", None)
        if settings.release_security_mode and event_id is None:
            raise AppError("forbidden", "Authenticated interaction ID required.")
        if settings.release_security_mode:
            try:
                await db.execute(
                    text("INSERT INTO admin_interactions(event_id,expires) VALUES (:id,:expires)"),
                    {"id": str(event_id), "expires": datetime.now(UTC) + timedelta(hours=1)},
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise AppError(
                    "duplicate_event", "Replayed administrator event rejected."
                ) from None
        grant_local_admin(db)
        db.info["admin_expiry"] = monotonic() + 900
        db.info["admin_guild"] = str(interaction.guild_id)
        yield db
