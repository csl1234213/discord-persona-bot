"""Migration/backup/restore on temporary synthetic SQLite only."""

import asyncio
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from app.core.authorization import grant_local_admin
from app.db.session import build_engine, build_sessionmaker
from app.schemas.persona import PersonaCreate
from app.services.persona_update_service import PersonaUpdateService


def migrate(root, directory, database, operation, revision):
    snippet = (
        "from alembic.config import Config; from alembic import command; "
        f"c=Config({str(root / 'alembic.ini')!r}); "
        f"c.set_main_option('script_location',{str(root / 'alembic')!r}); "
        f"command.{operation}(c,{revision!r})"
    )
    environment = {
        "PYTHONPATH": str(root),
        "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
    }
    if os.name == "nt":
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, "Synthetic migration failed: " + result.stderr[-2000:]


@pytest.mark.asyncio
async def test_migrate_backup_restore_and_schema_rollback(tmp_path):
    import app

    root = (await asyncio.to_thread(Path(app.__file__).resolve)).parents[1]
    database = tmp_path / "synthetic.db"
    await asyncio.to_thread(migrate, root, tmp_path, database, "upgrade", "c7f2a19d4e61")
    engine = build_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        async with build_sessionmaker(engine)() as db:
            grant_local_admin(db)
            await PersonaUpdateService(db).create_persona(
                PersonaCreate(display_name="Synthetic migration")
            )
    finally:
        await engine.dispose()
    await asyncio.to_thread(migrate, root, tmp_path, database, "upgrade", "head")

    def snapshot_and_verify():
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("INSERT INTO security_usage VALUES ('2026-01-01','global',3,100,0)")
            connection.commit()
            with closing(sqlite3.connect(tmp_path / "backup.db")) as backup:
                connection.backup(backup)
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    await asyncio.to_thread(snapshot_and_verify)
    await asyncio.to_thread(migrate, root, tmp_path, database, "downgrade", "c7f2a19d4e61")
    await asyncio.to_thread(migrate, root, tmp_path, database, "upgrade", "head")

    def restore_verify():
        # Restore to a NEW path, never overwrite an active SQLite/WAL set.
        with (
            closing(sqlite3.connect(tmp_path / "backup.db")) as backup,
            closing(sqlite3.connect(tmp_path / "restored.db")) as restored,
        ):
            backup.backup(restored)
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                restored.execute(
                    "SELECT requests FROM security_usage WHERE actor='global'"
                ).fetchone()[0]
                == 3
            )
            assert restored.execute("SELECT count(*) FROM personas").fetchone()[0] == 1
        with closing(sqlite3.connect(database)) as migrated:
            assert migrated.execute("SELECT count(*) FROM personas").fetchone()[0] == 1
            assert (
                migrated.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "e413a9d6c820"
            )

    await asyncio.to_thread(restore_verify)
