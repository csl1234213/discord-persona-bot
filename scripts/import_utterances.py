import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker
from app.services.attachment_import_service import AttachmentImportService

CONTENT_TYPES = {".jsonl": "application/x-ndjson", ".txt": "text/plain"}


async def import_file(
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    persona_id: UUID,
    version_id: UUID,
    verify: bool,
) -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    try:
        sessionmaker = build_sessionmaker(engine)
        async with sessionmaker() as db:
            grant_local_admin(db)
            importer = AttachmentImportService(db, settings)
            preview = await importer.preview(
                version_id=version_id,
                filename=filename,
                content_type=content_type,
                data=data,
                verify=verify,
            )
            records = await importer.persist(persona_id, version_id, preview)
            print(f"Imported={len(records)} duplicates={preview.duplicate_count} verified={verify}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import authorized UTF-8 utterances offline.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--persona-id", required=True, type=UUID)
    parser.add_argument("--version-id", required=True, type=UUID)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        import_file(
            filename=args.path.name,
            content_type=CONTENT_TYPES.get(args.path.suffix.lower()),
            data=args.path.read_bytes(),
            persona_id=args.persona_id,
            version_id=args.version_id,
            verify=args.verify,
        )
    )


if __name__ == "__main__":
    main()
