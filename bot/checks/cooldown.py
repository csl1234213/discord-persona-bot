import asyncio
import time
from collections import OrderedDict


class CooldownExceeded(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Retry after {retry_after:.1f} seconds")


class CooldownManager:
    def __init__(self, user_seconds: float, guild_seconds: float) -> None:
        self.user_seconds = user_seconds
        # Kept for settings compatibility. A guild-wide rejection bucket caused one
        # member's request to reject other members who spoke at the same time.
        self.guild_seconds = guild_seconds
        self._users: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str, guild_id: str | None) -> None:
        now = time.monotonic()
        async with self._lock:
            while self._users:
                key, stamp = next(iter(self._users.items()))
                if now - stamp < self.user_seconds and len(self._users) < 10000:
                    break
                self._users.pop(key)
            user_retry = self._users.get(user_id, 0) + self.user_seconds - now
            if user_retry > 0:
                raise CooldownExceeded(user_retry)
            self._users[user_id] = now
            self._users.move_to_end(user_id)
