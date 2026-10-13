"""Authority issued only by trusted local code after ingress permission checks."""

from functools import wraps
from time import monotonic

from app.core.errors import AppError

_ADMIN = object()
_ACTOR = object()


def bind_actor(db, context) -> None:
    """Only call with an identity obtained from the authenticated Discord event."""
    db.info["actor"] = (_ACTOR, context.guild_id, context.channel_id, context.discord_user_id)


def require_actor(db, context) -> None:
    expected = (_ACTOR, context.guild_id, context.channel_id, context.discord_user_id)
    if db.info.get("actor") != expected:
        raise AppError("forbidden", "Authenticated conversation identity required.")


def grant_local_admin(db) -> None:
    """For explicitly trusted CLI/bootstrap callers, never from user payload fields."""
    db.info["admin_authority"] = _ADMIN


def require_admin(db) -> None:
    if db.info.get("admin_authority") is not _ADMIN or monotonic() > db.info.get(
        "admin_expiry", float("inf")
    ):
        raise AppError("forbidden", "Administrator authority required.")


def admin_required(method):
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        require_admin(self.db)
        return await method(self, *args, **kwargs)

    return guarded
