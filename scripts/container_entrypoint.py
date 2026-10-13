import os
import subprocess
import sys


def main() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.release_security_mode:
        if settings.discord_bot_token is None:
            raise RuntimeError("Required Discord configuration missing: DISCORD_BOT_TOKEN")
        settings.validate_production_discord()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    command = sys.argv[1:] or [sys.executable, "-m", "bot.main"]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
