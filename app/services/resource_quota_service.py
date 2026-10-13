"""Atomic persistent UTC-day budgets. No prompt/response content in accounting."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.authorization import require_actor
from app.core.errors import AppError


class ResourceQuotaService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    async def reserve(self, context, *, requests=0, tokens=0):
        require_actor(self.db, context)
        s = self.settings
        if requests not in (0, 1) or not isinstance(tokens, int) or tokens < 0:
            raise AppError("invalid_budget", "Invalid quota reservation.")
        if s.llm_provider != "fake" and s.security_price_micros_per_token is None:
            raise AppError("pricing_required", "Verified provider price ceiling required.")
        day = datetime.now(UTC).date().isoformat()
        identity = ":".join((context.guild_id or "", context.channel_id, context.discord_user_id))
        actor = hashlib.sha256(identity.encode()).hexdigest()
        cost = tokens * (s.security_price_micros_per_token or 0)
        try:
            # Single-writer transaction spans BOTH limits; rollback prevents partial charging.
            await self.db.execute(text("BEGIN IMMEDIATE"))
            for key, req_max, token_max, cost_max in (
                (
                    "global",
                    s.security_global_daily_requests,
                    s.security_global_daily_tokens,
                    s.security_global_daily_cost_micros,
                ),
                (
                    actor,
                    s.security_user_daily_requests,
                    s.security_user_daily_tokens,
                    s.security_user_daily_cost_micros,
                ),
            ):
                await self.db.execute(
                    text(
                        "INSERT OR IGNORE INTO security_usage "
                        "(day, actor, requests, tokens, cost_micros) VALUES (:d,:a,0,0,0)"
                    ),
                    {"d": day, "a": key},
                )
                result = await self.db.execute(
                    text(
                        "UPDATE security_usage SET "
                        "requests=requests+:r,tokens=tokens+:t,cost_micros=cost_micros+:c "
                        "WHERE day=:d AND actor=:a AND requests+:r<=:rm "
                        "AND tokens+:t<=:tm AND cost_micros+:c<=:cm"
                    ),
                    {
                        "d": day,
                        "a": key,
                        "r": requests,
                        "t": tokens,
                        "c": cost,
                        "rm": req_max,
                        "tm": token_max,
                        "cm": cost_max,
                    },
                )
                if result.rowcount != 1:
                    raise AppError("quota_exceeded", "Daily security budget exhausted.")
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def reserve_llm(self, context, request):
        # UTF-8 bytes conservatively upper-bound text tokenization. Provider bills may
        # include other units: operator must set a proven worst-case price ceiling.
        parts = [request.system_prompt, request.user_message, request.corrective_instruction or ""]
        parts.extend(item.content for item in request.history)
        size = sum(len(item.encode("utf-8")) for item in parts) + 1024
        if size > self.settings.security_max_prompt_bytes:
            raise AppError("context_limit", "Model context exceeds security limit.")
        if not self.settings.llm_max_tokens or self.settings.llm_max_tokens > 2048:
            raise AppError("token_limit", "Finite model output limit required.")
        # Up to three providers, including failover; no refunds on uncertain failures.
        tokens = (size + self.settings.llm_max_tokens) * 6
        await self.reserve(context, tokens=tokens)
