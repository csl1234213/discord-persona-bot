import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.repositories.voice_asset_repository import VoiceAssetValidation
from bot.adapters.voice_asset_adapter import build_voice_asset_view
from bot.views.voice_asset_view import (
    PublicVoiceAssetButton,
    PublicVoiceAssetView,
    VoiceAssetView,
)


class AsyncContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ExplodingSessionFactory:
    def __call__(self):
        raise AssertionError("feature-off path must not open the database")


def result(*, origin="DIRECT_UTTERANCE", utterance_id=None, answer="原文"):
    return SimpleNamespace(
        response_origin=origin,
        selected_utterance_id=utterance_id,
        answer=answer,
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
@pytest.mark.parametrize("origin", ["CORPUS_COMPOSED", "SAFE_FALLBACK"])
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
@pytest.mark.parametrize("origin", ["DIRECT_UTTERANCE", "POLITICAL_CORPUS_UTTERANCE"])
async def test_corpus_original_with_valid_asset_gets_requester_bound_view(
    monkeypatch, tmp_path, origin
):
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
        result=result(origin=origin, utterance_id=utterance_id),
        requester_user_id=10,
    )
    assert isinstance(view, VoiceAssetView)
    assert view.requester_user_id == 10
    assert view.utterance_id == utterance_id
    resolver.assert_awaited_once_with(utterance_id, expected_text="原文")


@pytest.mark.asyncio
async def test_public_view_is_persistent_and_contains_only_the_asset_id(monkeypatch, tmp_path):
    utterance_id = uuid4()
    resolver = AsyncMock(return_value=VoiceAssetValidation(True, path=tmp_path / "001.mp3"))
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
        result=result(utterance_id=utterance_id),
        requester_user_id=10,
        public=True,
    )
    assert isinstance(view, PublicVoiceAssetView)
    assert view.timeout is None
    assert view.is_persistent()
    button = view.children[0]
    match = re.fullmatch(
        r"voice:v1:(?P<utterance_id>[0-9a-fA-F-]{36})", button.custom_id
    )
    assert match is not None
    restored = await PublicVoiceAssetButton.from_custom_id(
        SimpleNamespace(), button, match
    )
    assert restored.utterance_id == utterance_id


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


@pytest.mark.asyncio
async def test_public_button_attaches_audio_once_to_original_response(monkeypatch, tmp_path):
    audio = tmp_path / "001.mp3"
    audio.write_bytes(b"ID3fixture")
    utterance_id = uuid4()
    button = PublicVoiceAssetButton(utterance_id)
    resolver = AsyncMock(return_value=Path(audio))
    monkeypatch.setattr("bot.views.voice_asset_view._resolve_audio_path", resolver)
    client = SimpleNamespace(
        settings=SimpleNamespace(
            discord_guild_id="123",
            discord_chat_channel_id="456",
            voice_asset_feature_enabled=True,
        ),
        voice_cooldowns=SimpleNamespace(acquire=AsyncMock()),
        db_sessionmaker=object(),
    )
    message = SimpleNamespace(
        id=10_000_000 + utterance_id.int % 100_000,
        attachments=[],
        edit=AsyncMock(),
    )
    message.channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    interaction = SimpleNamespace(
        client=client,
        guild_id=123,
        channel_id=456,
        user=SimpleNamespace(id=789),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        message=message,
        edit_original_response=AsyncMock(),
    )

    await button.callback(interaction)

    client.voice_cooldowns.acquire.assert_awaited_once_with("789", "123")
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    resolver.assert_awaited_once_with(client.db_sessionmaker, client.settings, utterance_id)
    message.channel.fetch_message.assert_awaited_once_with(message.id)
    message.edit.assert_awaited_once()
    uploaded = message.edit.await_args.kwargs["attachments"][-1]
    assert uploaded.filename == "001.mp3"
    assert uploaded.fp.closed
    assert message.edit.await_args.kwargs["view"] is None
    interaction.edit_original_response.assert_awaited_once_with(
        content="语音已附在上方回复中，可直接播放。"
    )


@pytest.mark.asyncio
async def test_public_button_repeat_click_does_not_upload_a_second_copy(monkeypatch, tmp_path):
    audio = tmp_path / "051.mp3"
    audio.write_bytes(b"ID3fixture")
    utterance_id = uuid4()
    button = PublicVoiceAssetButton(utterance_id)
    resolver = AsyncMock(return_value=Path(audio))
    monkeypatch.setattr("bot.views.voice_asset_view._resolve_audio_path", resolver)
    attachment = SimpleNamespace(filename="051.mp3")
    message = SimpleNamespace(
        id=20_000_000 + utterance_id.int % 100_000,
        attachments=[attachment],
        edit=AsyncMock(),
    )
    message.channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    client = SimpleNamespace(
        settings=SimpleNamespace(
            discord_guild_id="123",
            discord_chat_channel_id="456",
            voice_asset_feature_enabled=True,
        ),
        voice_cooldowns=SimpleNamespace(acquire=AsyncMock()),
        db_sessionmaker=object(),
    )
    interaction = SimpleNamespace(
        client=client,
        guild_id=123,
        channel_id=456,
        user=SimpleNamespace(id=789),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        message=message,
        edit_original_response=AsyncMock(),
    )

    await button.callback(interaction)

    message.channel.fetch_message.assert_awaited_once_with(message.id)
    message.edit.assert_not_awaited()
    interaction.edit_original_response.assert_awaited_once_with(
        content="语音已附在上方回复中，可直接播放。"
    )


@pytest.mark.asyncio
async def test_public_button_rejects_other_channels_before_serving_audio(monkeypatch):
    button = PublicVoiceAssetButton(uuid4())
    cooldown = AsyncMock()
    interaction = SimpleNamespace(
        client=SimpleNamespace(
            settings=SimpleNamespace(
                discord_guild_id="123",
                discord_chat_channel_id="456",
                voice_asset_feature_enabled=True,
            ),
            voice_cooldowns=SimpleNamespace(acquire=cooldown),
        ),
        guild_id=123,
        channel_id=999,
        user=SimpleNamespace(id=789),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await button.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    cooldown.assert_not_awaited()
