from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.repositories.voice_asset_repository import VoiceAssetValidation
from bot.adapters.voice_asset_adapter import build_voice_asset_view
from bot.views.voice_asset_view import VoiceAssetView


class AsyncContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ExplodingSessionFactory:
    def __call__(self):
        raise AssertionError("feature-off path must not open the database")


def result(*, origin="DIRECT_UTTERANCE", utterance_id=None):
    return SimpleNamespace(
        response_origin=origin,
        selected_utterance_id=utterance_id,
    )


@pytest.mark.asyncio
async def test_feature_off_returns_without_database_or_file_access():
    view = await build_voice_asset_view(
        db_sessionmaker=ExplodingSessionFactory(),
        settings=Settings(
            discord_sync_commands_on_startup=False,
            voice_asset_feature_enabled=False,
        ),
        result=result(utterance_id=uuid4()),
        requester_user_id=10,
    )
    assert view is None


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["LLM_GENERATED", "SAFE_FALLBACK"])
async def test_non_original_responses_never_get_voice_button(origin):
    view = await build_voice_asset_view(
        db_sessionmaker=ExplodingSessionFactory(),
        settings=Settings(
            discord_sync_commands_on_startup=False,
            voice_asset_feature_enabled=True,
        ),
        result=result(origin=origin, utterance_id=uuid4()),
        requester_user_id=10,
    )
    assert view is None


@pytest.mark.asyncio
async def test_direct_original_with_valid_asset_gets_requester_bound_view(monkeypatch, tmp_path):
    utterance_id = uuid4()
    audio = tmp_path / "001.mp3"
    audio.write_bytes(b"ID3fixture")
    validation = VoiceAssetValidation(True, path=audio)
    resolver = AsyncMock(return_value=validation)
    monkeypatch.setattr(
        "bot.adapters.voice_asset_adapter.UtteranceVoiceAssetRepository.resolve_audio_path",
        resolver,
    )
    settings = Settings(
        discord_sync_commands_on_startup=False,
        voice_asset_feature_enabled=True,
        voice_asset_root=tmp_path,
    )
    view = await build_voice_asset_view(
        db_sessionmaker=lambda: AsyncContext(),
        settings=settings,
        result=result(utterance_id=utterance_id),
        requester_user_id=10,
    )
    assert isinstance(view, VoiceAssetView)
    assert view.requester_user_id == 10
    assert view.utterance_id == utterance_id
    resolver.assert_awaited_once_with(utterance_id)


@pytest.mark.asyncio
async def test_invalid_asset_omits_button(monkeypatch, tmp_path):
    resolver = AsyncMock(return_value=VoiceAssetValidation(False, reason="audio_hash_mismatch"))
    monkeypatch.setattr(
        "bot.adapters.voice_asset_adapter.UtteranceVoiceAssetRepository.resolve_audio_path",
        resolver,
    )
    view = await build_voice_asset_view(
        db_sessionmaker=lambda: AsyncContext(),
        settings=Settings(
            discord_sync_commands_on_startup=False,
            voice_asset_feature_enabled=True,
            voice_asset_root=tmp_path,
        ),
        result=result(utterance_id=uuid4()),
        requester_user_id=10,
    )
    assert view is None


@pytest.mark.asyncio
async def test_only_requester_can_click_voice_button():
    view = VoiceAssetView(
        requester_user_id=10,
        utterance_id=uuid4(),
        db_sessionmaker=lambda: AsyncContext(),
        settings=Settings(discord_sync_commands_on_startup=False),
    )
    denied = SimpleNamespace(
        user=SimpleNamespace(id=11),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    allowed = SimpleNamespace(user=SimpleNamespace(id=10))
    assert not await view.interaction_check(denied)
    assert denied.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await view.interaction_check(allowed)


@pytest.mark.asyncio
async def test_click_uploads_exact_file_without_rerunning_chat(monkeypatch, tmp_path):
    audio = tmp_path / "001.mp3"
    audio.write_bytes(b"ID3fixture")
    view = VoiceAssetView(
        requester_user_id=10,
        utterance_id=uuid4(),
        db_sessionmaker=lambda: AsyncContext(),
        settings=Settings(discord_sync_commands_on_startup=False),
    )
    resolver = AsyncMock(return_value=Path(audio))
    chat_spy = AsyncMock()
    llm_spy = AsyncMock()
    monkeypatch.setattr(view, "_resolve_audio_path", resolver)
    monkeypatch.setattr("app.services.persona_chat_service.PersonaChatService.chat", chat_spy)
    monkeypatch.setattr("app.llm.fake.FakeLLMClient.generate", llm_spy)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await view.play_voice.callback(interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    resolver.assert_awaited_once_with()
    uploaded = interaction.followup.send.await_args.kwargs["file"]
    assert uploaded.filename == "001.mp3"
    assert uploaded.fp.closed
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    chat_spy.assert_not_awaited()
    llm_spy.assert_not_awaited()

    await view.play_voice.callback(interaction)
    assert interaction.response.send_message.await_args.args[0] == "操作过快，请稍后再试。"
    assert resolver.await_count == 1


@pytest.mark.asyncio
async def test_timeout_disables_button_and_edits_message():
    view = VoiceAssetView(
        requester_user_id=10,
        utterance_id=uuid4(),
        db_sessionmaker=lambda: AsyncContext(),
        settings=Settings(discord_sync_commands_on_startup=False),
    )
    view.message = SimpleNamespace(edit=AsyncMock())
    await view.on_timeout()
    assert all(child.disabled for child in view.children)
    view.message.edit.assert_awaited_once_with(view=view)
