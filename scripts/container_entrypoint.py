import os
import subprocess
import sys


def main() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    command = sys.argv[1:] or [sys.executable, "-m", "bot.main"]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
