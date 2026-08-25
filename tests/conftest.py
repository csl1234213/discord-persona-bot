from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker, init_database


@pytest.fixture(autouse=True)
def isolate_tests_from_real_dotenv(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest_asyncio.fixture
async def db_sessionmaker() -> AsyncGenerator:
    cache = Path(".cache/tests")
    cache.mkdir(parents=True, exist_ok=True)
    database = (cache / f"test-{uuid4()}.sqlite3").resolve()
    engine = build_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    await init_database(engine)
    sessionmaker = build_sessionmaker(engine)
    yield sessionmaker
    await engine.dispose()
    database.unlink(missing_ok=True)
