from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base


def build_engine(database_url: str, *, max_bytes: int | None = None) -> AsyncEngine:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    if max_bytes is not None and database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA secure_delete=ON")
            cursor.execute("PRAGMA busy_timeout=2000")
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.execute("PRAGMA page_size")
            size = cursor.fetchone()[0]
            cursor.execute(f"PRAGMA max_page_count={max_bytes // size:d}")
            if cursor.fetchone()[0] * size > max_bytes:
                cursor.close()
                raise RuntimeError("Database exceeds configured capacity; offline cleanup required")
            cursor.close()

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def init_database(engine: AsyncEngine) -> None:
    from app.db.models import domain  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
