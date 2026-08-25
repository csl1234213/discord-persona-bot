import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository
from app.schemas.chat import ChatResult
from bot.views.voice_asset_view import VoiceAssetView

logger = logging.getLogger(__name__)


async def build_voice_asset_view(
    *,
    db_sessionmaker: async_sessionmaker,
    settings: Settings,
    result: ChatResult,
    requester_user_id: int,
) -> VoiceAssetView | None:
    if not settings.voice_asset_feature_enabled:
        return None
    if result.response_origin != "DIRECT_UTTERANCE":
        return None
    if result.selected_utterance_id is None:
        return None
    async with db_sessionmaker() as db:
        validation = await UtteranceVoiceAssetRepository(db, settings).resolve_audio_path(
            result.selected_utterance_id
        )
    if not validation.valid:
        logger.info(
            "Voice button omitted utterance_id=%s reason=%s",
            result.selected_utterance_id,
            validation.reason,
        )
        return None
    return VoiceAssetView(
        requester_user_id=requester_user_id,
        utterance_id=result.selected_utterance_id,
        db_sessionmaker=db_sessionmaker,
        settings=settings,
    )
