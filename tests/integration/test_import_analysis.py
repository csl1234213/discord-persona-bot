import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db.models import Utterance
from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.attachment_import_service import AttachmentImportService
from app.services.language_analysis_service import LanguageAnalysisService
from app.services.persona_update_service import PersonaUpdateService
from bot.cogs.utterance_admin import UtteranceAdminCog
from bot.views.confirmation_view import ConfirmationView


@pytest.mark.asyncio
async def test_attachment_preview_deduplicate_persist_and_explicit_verification(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="导入人物")
        )
        await PersonaUpdateService(db).add_utterance(
            persona.id, version.id, UtteranceCreate(text="已有言论")
        )
        service = AttachmentImportService(db, Settings(max_import_lines=10))
        data = "\n".join(
            [
                json.dumps({"text": "已有言论"}, ensure_ascii=False),
                json.dumps({"text": "新言论"}, ensure_ascii=False),
                json.dumps({"text": "新言论"}, ensure_ascii=False),
            ]
        ).encode()
        preview = await service.preview(
            version_id=version.id,
            filename="utterances.jsonl",
            content_type="application/x-ndjson",
            data=data,
            verify=True,
        )
        assert len(preview.items) == 1
        assert preview.duplicate_count == 2
        assert preview.items[0].verified is True
        records = await service.persist(persona.id, version.id, preview)
        assert records[0].verified is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "data", "settings", "match"),
    [
        ("bad.exe", "application/octet-stream", b"x", Settings(), "Only UTF-8"),
        ("big.txt", "text/plain", b"12345", Settings(max_import_bytes=4), "file-size"),
        ("bad.txt", "text/plain", b"\xff", Settings(), "valid UTF-8"),
        ("many.txt", "text/plain", b"1\n2", Settings(max_import_lines=1), "line-count"),
        ("empty.txt", "text/plain", b"\n", Settings(), "no importable"),
        ("bad.jsonl", "application/json", b"not-json", Settings(), "line 1"),
    ],
)
async def test_attachment_rejections(
    db_sessionmaker, filename, content_type, data, settings, match
):
    async with db_sessionmaker() as db:
        _, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="拒绝测试")
        )
        with pytest.raises(ValidationFailed, match=match):
            await AttachmentImportService(db, settings).preview(
                version_id=version.id,
                filename=filename,
                content_type=content_type,
                data=data,
            )


def test_language_analysis_only_proposes_rules():
    items = [
        Utterance(text="朋友，先别急，慢慢来。", verified=True),
        Utterance(text="朋友，先别急，先看清。", verified=True),
        Utterance(text="不应使用", verified=False),
    ]
    result = LanguageAnalysisService().analyze(items)
    assert result.published is False
    assert result.sentence_length["count"] == 2
    assert any(item["text"] == "先别急" for item in result.frequent_phrases)


@pytest.mark.asyncio
async def test_discord_attachment_command_preview_confirm_and_analysis(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="命令导入")
        )
    bot = SimpleNamespace(
        db_sessionmaker=db_sessionmaker,
        settings=Settings(max_import_lines=10),
    )
    cog = UtteranceAdminCog(bot)
    current = SimpleNamespace(
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    attachment = SimpleNamespace(
        filename="items.txt",
        content_type="text/plain",
        read=AsyncMock(return_value="第一条\n第二条".encode()),
    )
    await cog.import_attachment.callback(
        cog, current, str(persona.id), str(version.id), attachment, False
    )
    view = current.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, ConfirmationView)
    confirm_interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    await view.on_confirm_callback(confirm_interaction)
    async with db_sessionmaker() as db:
        items = await UtteranceRepository(db).list_for_version(version.id)
        items[0].verified = True
        items[1].verified = True
        await db.commit()
    await cog.analyze.callback(cog, current, str(version.id))
    embed = current.response.send_message.await_args.kwargs["embed"]
    assert "尚未发布" in embed.description


@pytest.mark.asyncio
async def test_confirmation_view_restricts_other_users():
    callback = AsyncMock()
    view = ConfirmationView(owner_user_id=1, on_confirm=callback)
    denied = SimpleNamespace(
        user=SimpleNamespace(id=2),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert not await view.interaction_check(denied)
    denied.response.send_message.assert_awaited_once()
    allowed = SimpleNamespace(user=SimpleNamespace(id=1))
    assert await view.interaction_check(allowed)
