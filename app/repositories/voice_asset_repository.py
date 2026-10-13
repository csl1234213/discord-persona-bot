import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Persona, PersonaVersion, Utterance, UtteranceVoiceAsset


@dataclass(frozen=True)
class VoiceAssetValidation:
    valid: bool
    path: Path | None = None
    reason: str | None = None


class UtteranceVoiceAssetRepository:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def add(self, asset: UtteranceVoiceAsset) -> UtteranceVoiceAsset:
        self.db.add(asset)
        await self.db.flush()
        return asset

    async def get_by_utterance_id(self, utterance_id: UUID) -> UtteranceVoiceAsset | None:
        return await self.db.scalar(
            select(UtteranceVoiceAsset)
            .where(UtteranceVoiceAsset.utterance_id == utterance_id)
            .limit(1)
        )

    async def get_by_relative_path(self, relative_path: str) -> UtteranceVoiceAsset | None:
        return await self.db.scalar(
            select(UtteranceVoiceAsset)
            .where(UtteranceVoiceAsset.audio_relative_path == relative_path)
            .limit(1)
        )

    async def validate_asset(self, asset: UtteranceVoiceAsset) -> VoiceAssetValidation:
        utterance = await self.db.get(Utterance, asset.utterance_id)
        if utterance is None:
            return VoiceAssetValidation(False, reason="utterance_missing")
        if self.settings.release_security_mode:
            version = await self.db.get(PersonaVersion, utterance.persona_version_id)
            persona = await self.db.get(Persona, version.persona_id) if version else None
            if (
                not utterance.verified
                or not version
                or version.status != "published"
                or not persona
                or persona.active_version_id != version.id
            ):
                return VoiceAssetValidation(False, reason="inactive_or_unverified_asset")
        if self._sha256_text(utterance.text) != asset.text_sha256:
            return VoiceAssetValidation(False, reason="text_hash_mismatch")
        root = self.settings.voice_asset_root
        if root is None:
            return VoiceAssetValidation(False, reason="root_not_configured")
        relative = PurePosixPath(asset.audio_relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or ":" in relative.parts[0]
        ):
            return VoiceAssetValidation(False, reason="invalid_relative_path")
        try:
            resolved_root = root.expanduser().resolve(strict=True)
            candidate = resolved_root.joinpath(*relative.parts).resolve(strict=True)
        except (OSError, RuntimeError):
            return VoiceAssetValidation(False, reason="file_missing")
        try:
            common = os.path.commonpath((str(resolved_root), str(candidate)))
        except ValueError:
            return VoiceAssetValidation(False, reason="path_outside_root")
        if os.path.normcase(common) != os.path.normcase(str(resolved_root)):
            return VoiceAssetValidation(False, reason="path_outside_root")
        if not candidate.is_file():
            return VoiceAssetValidation(False, reason="file_missing")
        try:
            size = candidate.stat().st_size
        except OSError:
            return VoiceAssetValidation(False, reason="file_unreadable")
        if size <= 0 or size > 8 * 1024 * 1024:
            return VoiceAssetValidation(False, reason="empty_file")
        if size != asset.size_bytes:
            return VoiceAssetValidation(False, reason="audio_size_mismatch")
        if candidate.suffix.casefold() != asset.file_extension.casefold():
            return VoiceAssetValidation(False, reason="extension_mismatch")
        try:
            actual_audio_hash = self._sha256_file(candidate)
        except OSError:
            return VoiceAssetValidation(False, reason="file_unreadable")
        if actual_audio_hash != asset.audio_sha256:
            return VoiceAssetValidation(False, reason="audio_hash_mismatch")
        return VoiceAssetValidation(True, path=candidate)

    async def resolve_audio_path(
        self, utterance_id: UUID, *, expected_text: str | None = None
    ) -> VoiceAssetValidation:
        asset = await self.get_by_utterance_id(utterance_id)
        if asset is None:
            return VoiceAssetValidation(False, reason="asset_missing")
        if expected_text is not None:
            utterance = await self.db.get(Utterance, utterance_id)
            if utterance is None or utterance.text != expected_text:
                return VoiceAssetValidation(False, reason="response_text_mismatch")
        return await self.validate_asset(asset)

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
