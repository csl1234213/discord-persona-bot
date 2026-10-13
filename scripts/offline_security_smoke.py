"""Network-free smoke from the release image, using only synthetic demo data."""

import asyncio
import json
import signal

from sqlalchemy import select

from app.core.authorization import bind_actor
from app.core.config import Settings
from app.db.models import Persona
from app.schemas.chat import DiscordContext
from bot.client import create_bot
from scripts.seed_demo_persona import DEMO_UTTERANCES, seed


async def main():
    await seed("Public synthetic demo", True)
    settings = Settings()
    bot = create_bot(settings)
    await bot.setup_hook()
    try:
        async with bot.db_sessionmaker() as db:
            persona = await db.scalar(
                select(Persona).where(Persona.display_name == "Public synthetic demo")
            )
            from app.services.persona_chat_service import PersonaChatService

            service = PersonaChatService(db, bot.llm_client, settings, bot.llm_semaphore)
            context = DiscordContext(guild_id="100", channel_id="200", discord_user_id="300")
            bind_actor(db, context)
            result = await service.chat(DEMO_UTTERANCES[0], context)
            assert result.answer
            try:
                await service.chat("blocked", context.model_copy(update={"guild_id": "999"}))
            except Exception as exc:
                assert getattr(exc, "code", None) == "forbidden"
            else:
                raise AssertionError("Cross-guild request was accepted")
            print(
                json.dumps(
                    {
                        "smoke": "PASS",
                        "persona_count": 1,
                        "version_bound": bool(persona.active_version_id),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
        loop.add_signal_handler(signal.SIGINT, stopped.set)
        await stopped.wait()
    finally:
        await bot.close()
        print("SHUTDOWN_FLUSH=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
