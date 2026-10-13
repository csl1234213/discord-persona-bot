"""Runtime negative authorization matrix; no Discord network operations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.numbered_corpus_import_service import NumberedCorpusImportService
from app.services.persona_configuration_service import PersonaConfigurationService
from app.services.persona_corpus_append_service import PersonaCorpusAppendService
from app.services.persona_corpus_replace_service import PersonaCorpusReplaceService
from app.services.persona_update_service import PersonaUpdateService
from app.services.persona_version_consolidation_service import PersonaVersionConsolidationService
from app.services.voice_asset_import_service import VoiceAssetImportService
from bot.checks.security import admin_session
from bot.cogs.persona_admin import PersonaAdminCog
from bot.cogs.style_admin import StyleAdminCog
from bot.views.confirmation_view import ConfirmationView


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "create_persona",
        "create_draft",
        "add_utterance",
        "set_verified",
        "delete_utterance",
        "publish",
        "activate",
        "rollback",
        "select_for_guild",
    ],
)
async def test_all_admin_update_writes_refuse_untrusted_service_calls(name):
    db = SimpleNamespace(info={}, commit=AsyncMock(), execute=AsyncMock())
    with pytest.raises(AppError, match="Administrator"):
        await getattr(PersonaUpdateService(db), name)()
    db.commit.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service,method",
    [
        (VoiceAssetImportService, "apply"),
        (VoiceAssetImportService, "preview"),
        (NumberedCorpusImportService, "apply"),
        (PersonaCorpusAppendService, "append_text_file"),
        (PersonaCorpusReplaceService, "replace_utterance_containing"),
        (PersonaVersionConsolidationService, "consolidate"),
        (PersonaConfigurationService, "update"),
        (PersonaConfigurationService, "delete_address"),
    ],
)
async def test_all_auxiliary_write_services_require_authority(service, method):
    db = SimpleNamespace(info={}, commit=AsyncMock())
    instance = (
        service(db, Settings(_env_file=None))
        if service in {VoiceAssetImportService, PersonaCorpusAppendService}
        else service(db)
    )
    with pytest.raises(AppError, match="Administrator"):
        await getattr(instance, method)()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_expired_service_authority_denied():
    db = SimpleNamespace(info={})
    grant_local_admin(db)
    db.info["admin_expiry"] = -1
    with pytest.raises(AppError, match="Administrator"):
        await PersonaUpdateService(db).rollback(uuid4())


@pytest.mark.asyncio
async def test_admin_interaction_replay_is_durable(db_sessionmaker):
    policy = Settings(
        _env_file=None,
        release_security_mode=True,
        discord_guild_id="10",
        discord_chat_channel_id="20",
        discord_owner_user_ids="30",
    )
    bot = SimpleNamespace(settings=policy, db_sessionmaker=db_sessionmaker)
    event = SimpleNamespace(
        id=100,
        created_at=datetime.now(UTC),
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30),
    )
    async with admin_session(bot, event):
        pass
    with pytest.raises(AppError, match="Replayed"):
        async with admin_session(bot, event):
            pytest.fail("duplicate event admitted")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args",
    [
        ("publish", (str(uuid4()), str(uuid4()))),
        ("activate", (str(uuid4()), str(uuid4()))),
        ("rollback", (str(uuid4()),)),
        ("select_persona", (str(uuid4()),)),
        ("export", (str(uuid4()),)),
    ],
)
@pytest.mark.parametrize("who", ["member", "other_admin", "other_guild", "other_channel"])
async def test_persona_slash_direct_callbacks_cannot_bypass_auth(command, args, who):
    bot = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            discord_guild_id="10",
            discord_chat_channel_id="20",
            discord_owner_user_ids="30",
        ),
        db_sessionmaker=MagicMock(),
    )
    cog = PersonaAdminCog(bot)
    event = SimpleNamespace(
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    if who in {"member", "other_admin"}:
        event.user.id = 31
    if who == "other_guild":
        event.guild_id = 11
    if who == "other_channel":
        event.channel_id = 21
    with pytest.raises(AppError):
        await getattr(cog, command).callback(cog, event, *args)
    bot.db_sessionmaker.assert_not_called()


@pytest.mark.asyncio
async def test_cross_persona_utterance_modify_delete_and_immutable_version(db_sessionmaker):
    async with db_sessionmaker() as db:
        service = PersonaUpdateService(db)
        p, v = await service.create_persona(PersonaCreate(display_name="One"))
        p2, v2 = await service.create_persona(PersonaCreate(display_name="Two"))
        u = await service.add_utterance(
            p2.id, v2.id, UtteranceCreate(text="synthetic", verified=True)
        )
        with pytest.raises(AppError):
            await service.set_verified(p.id, v.id, u.id, False)
        with pytest.raises(AppError):
            await service.delete_utterance(p.id, v.id, u.id)
        with pytest.raises(AppError):
            await PersonaConfigurationService(db).update(
                p.id, v.id, "utterance", {"text": "bad"}, record_id=u.id
            )
        await service.publish(p2.id, v2.id)
        with pytest.raises(AppError, match="immutable"):
            await PersonaConfigurationService(db).update(
                p2.id, v2.id, "style", {"emoji_policy": "never"}
            )
        with pytest.raises(AppError):
            await service.activate(p.id, v2.id)
        db.info["admin_guild"] = "10"
        with pytest.raises(AppError, match="ownership"):
            await service.select_for_guild(p2.id, "11")


@pytest.mark.asyncio
async def test_modal_origin_blocks_other_owner_and_expiry(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="Modal")
        )
    policy = Settings(
        _env_file=None,
        discord_guild_id="10",
        discord_chat_channel_id="20",
        discord_owner_user_ids="30,31",
    )
    cog = StyleAdminCog(SimpleNamespace(settings=policy, db_sessionmaker=db_sessionmaker))
    original = SimpleNamespace(
        guild_id=10,
        channel_id=20,
        user=SimpleNamespace(id=30),
        response=SimpleNamespace(send_modal=AsyncMock()),
    )
    await cog.style_edit.callback(cog, original, str(persona.id), str(version.id))
    modal = original.response.send_modal.await_args.args[0]
    with pytest.raises(AppError, match="actor"):
        await modal.on_submit_callback(
            SimpleNamespace(guild_id=10, channel_id=20, user=SimpleNamespace(id=31)), {}
        )


@pytest.mark.asyncio
async def test_confirmation_direct_button_replay_runs_once():
    save = AsyncMock()
    view = ConfirmationView(owner_user_id=30, on_confirm=save)
    event = SimpleNamespace(user=SimpleNamespace(id=30))
    await view.confirm.callback(event)
    await view.confirm.callback(event)
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_voice_asset_must_be_verified_and_current(db_sessionmaker, tmp_path):
    import asyncio
    import hashlib

    from app.db.models import UtteranceVoiceAsset
    from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository

    audio = tmp_path / "synthetic.mp3"
    payload = b"ID3synthetic-not-a-real-recording"
    await asyncio.to_thread(audio.write_bytes, payload)
    async with db_sessionmaker() as db:
        service = PersonaUpdateService(db)
        persona, version = await service.create_persona(
            PersonaCreate(display_name="Voice synthetic")
        )
        utterance = await service.add_utterance(
            persona.id, version.id, UtteranceCreate(text="synthetic", verified=True)
        )
        await service.publish(persona.id, version.id)
        asset = UtteranceVoiceAsset(
            utterance_id=utterance.id,
            audio_relative_path="synthetic.mp3",
            text_sha256=hashlib.sha256(b"synthetic").hexdigest(),
            audio_sha256=hashlib.sha256(payload).hexdigest(),
            media_type="audio/mpeg",
            file_extension=".mp3",
            size_bytes=len(payload),
        )
        db.add(asset)
        await db.commit()
        repository = UtteranceVoiceAssetRepository(
            db, Settings(_env_file=None, release_security_mode=True, voice_asset_root=tmp_path)
        )
        assert (await repository.validate_asset(asset)).valid
        utterance.verified = False
        await db.flush()
        assert not (await repository.validate_asset(asset)).valid
        utterance.verified = True
        persona.active_version_id = None
        await db.flush()
        assert not (await repository.validate_asset(asset)).valid


@pytest.mark.asyncio
async def test_address_service_rejects_cross_guild_creation_and_mutation(db_sessionmaker):
    async with db_sessionmaker() as db:
        persona, version = await PersonaUpdateService(db).create_persona(
            PersonaCreate(display_name="Scoped")
        )
        db.info["admin_guild"] = "10"
        service = PersonaConfigurationService(db)
        with pytest.raises(AppError, match="ownership"):
            await service.update(
                persona.id, version.id, "address", {"address": "demo"}, guild_id="11"
            )
        address = await service.update(
            persona.id, version.id, "address", {"address": "demo"}, guild_id="10"
        )
        address.guild_id = "11"
        await db.commit()
        with pytest.raises(AppError, match="ownership"):
            await service.update(
                persona.id, version.id, "address", {"address": "other"}, record_id=address.id
            )
        with pytest.raises(AppError, match="ownership"):
            await service.delete_address(persona.id, version.id, address.id)
