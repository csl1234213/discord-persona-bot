import json
from pathlib import PurePath
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db.models import Utterance
from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.persona import ImportPreview, UtteranceCreate
from app.services.persona_update_service import PersonaUpdateService

ALLOWED_EXTENSIONS = {".jsonl", ".txt"}
ALLOWED_MIME_TYPES = {
    "application/json",
    "application/jsonl",
    "application/x-ndjson",
    "text/plain",
    "text/jsonl",
    None,
}


class AttachmentImportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.utterances = UtteranceRepository(db)

    async def preview(
        self,
        *,
        version_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
        verify: bool = False,
    ) -> ImportPreview:
        extension = PurePath(filename).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS or content_type not in ALLOWED_MIME_TYPES:
            raise ValidationFailed("Only UTF-8 .jsonl and .txt attachments are accepted.")
        if len(data) > self.settings.max_import_bytes:
            raise ValidationFailed("Attachment exceeds the configured file-size limit.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed("Attachment must be valid UTF-8.") from exc
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > self.settings.max_import_lines:
            raise ValidationFailed("Attachment exceeds the configured line-count limit.")
        if not lines:
            raise ValidationFailed("Attachment contains no importable lines.")
        existing = {
            item.text.strip() for item in await self.utterances.list_for_version(version_id)
        }
        seen = set(existing)
        items: list[UtteranceCreate] = []
        duplicates = 0
        for line_number, line in enumerate(lines, start=1):
            try:
                if extension == ".jsonl":
                    payload = json.loads(line)
                    payload["verified"] = verify
                    item = UtteranceCreate.model_validate(payload)
                else:
                    item = UtteranceCreate(text=line.strip(), verified=verify)
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                raise ValidationFailed(f"Invalid record on line {line_number}.") from exc
            normalized = item.text.strip()
            if normalized in seen:
                duplicates += 1
                continue
            seen.add(normalized)
            items.append(item)
        if not items:
            raise ValidationFailed("Every attachment record is a duplicate.")
        return ImportPreview(
            filename=filename,
            items=items,
            duplicate_count=duplicates,
            line_count=len(lines),
        )

    async def persist(
        self, persona_id: UUID, version_id: UUID, preview: ImportPreview
    ) -> list[Utterance]:
        await PersonaUpdateService(self.db).require_draft(persona_id, version_id)
        records = [
            Utterance(persona_version_id=version_id, **item.model_dump()) for item in preview.items
        ]
        self.db.add_all(records)
        await self.db.commit()
        return records
