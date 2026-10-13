import asyncio

import pytest

from app.services.conversation_lock_manager import ConversationLockManager


@pytest.mark.asyncio
async def test_same_key_is_serialized_and_registry_is_cleaned():
    manager = ConversationLockManager()
    entered = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []
    key = ("guild", "channel", "user")

    async def first() -> None:
        async with manager.hold(key):
            order.append("first-enter")
            entered.set()
            await release.wait()
            order.append("first-exit")

    async def second() -> None:
        await entered.wait()
        async with manager.hold(key):
            order.append("second-enter")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await entered.wait()
    await asyncio.sleep(0)
    assert order == ["first-enter"]
    release.set()
    await asyncio.gather(first_task, second_task)

    assert order == ["first-enter", "first-exit", "second-enter"]
    assert manager._entries == {}


@pytest.mark.asyncio
async def test_different_keys_do_not_block_each_other():
    manager = ConversationLockManager()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(key, entered) -> None:
        async with manager.hold(key):
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold(("guild", "channel", "one"), first_entered))
    second = asyncio.create_task(hold(("guild", "channel", "two"), second_entered))
    await asyncio.wait_for(asyncio.gather(first_entered.wait(), second_entered.wait()), 1)
    release.set()
    await asyncio.gather(first, second)
    assert manager._entries == {}


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_registry_entry():
    manager = ConversationLockManager()
    owner_entered = asyncio.Event()
    release = asyncio.Event()
    key = ("guild", "channel", "user")

    async def owner() -> None:
        async with manager.hold(key):
            owner_entered.set()
            await release.wait()

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()

    async def wait_for_lock() -> None:
        async with manager.hold(key):
            pass

    waiter = asyncio.create_task(wait_for_lock())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await owner_task
    assert manager._entries == {}
