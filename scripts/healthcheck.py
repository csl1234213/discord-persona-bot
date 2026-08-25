import asyncio

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import build_engine


async def check_database() -> None:
    engine = build_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check_database())
