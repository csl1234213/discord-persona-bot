import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from bot.client import create_bot


async def run_smoke_test() -> None:
    settings = get_settings().model_copy(update={"discord_sync_commands_on_startup": False})
    bot = create_bot(settings)
    try:
        await bot.setup_hook()
        command_count = len(bot.tree.get_commands())
        if not bot.ready_for_discord or command_count == 0:
            raise RuntimeError("Discord bot smoke initialization did not become ready.")
        print(f"DISCORD_READY_SMOKE=true commands={command_count}")
    finally:
        await bot.close()


def main() -> None:
    settings = get_settings()
    settings.ensure_local_directories()
    configure_logging(settings.log_level)
    if settings.bot_smoke_test_mode:
        asyncio.run(run_smoke_test())
        return
    if settings.discord_bot_token is None:
        raise RuntimeError("DISCORD_BOT_TOKEN is required to connect to Discord.")
    bot = create_bot(settings)
    bot.run(settings.discord_bot_token.get_secret_value(), log_handler=None)


if __name__ == "__main__":
    main()
