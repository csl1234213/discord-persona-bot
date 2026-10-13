import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

ConversationKey = tuple[str | None, str, str]


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class ConversationLockManager:
    def __init__(self) -> None:
        self._registry_lock = asyncio.Lock()
        self._entries: dict[ConversationKey, _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, key: ConversationKey) -> AsyncIterator[None]:
        async with self._registry_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry(asyncio.Lock())
                self._entries[key] = entry
            entry.users += 1

        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._registry_lock:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(key, None)
