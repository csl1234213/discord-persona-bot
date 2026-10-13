import gc
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker, init_database


@pytest.fixture(autouse=True)
def isolate_tests_from_real_dotenv(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def _new_test_database_path() -> Path:
    cache = Path(".cache/tests")
    cache.mkdir(parents=True, exist_ok=True)
    return (cache / f"test-{uuid4()}.sqlite3").resolve()


@pytest_asyncio.fixture
async def db_sessionmaker() -> AsyncGenerator:
    database = _new_test_database_path()
    engine = build_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    await init_database(engine)
    sessionmaker = build_sessionmaker(engine)
    async with sessionmaker() as bootstrap:
        grant_local_admin(bootstrap)
        sessionmaker.configure(info=dict(bootstrap.info))
    yield sessionmaker
    await engine.dispose()
    # Error tracebacks may retain SQLite cursors until cyclic GC on Windows.
    gc.collect()
    database.unlink(missing_ok=True)
