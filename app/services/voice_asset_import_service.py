import hashlib
import mimetypes
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Persona, PersonaVersion, Utterance, UtteranceVoiceAsset
from app.repositories.voice_asset_repository import UtteranceVoiceAssetRepository
from app.services.numbered_corpus_import_service import NumberedCorpusImportService

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


@dataclass(frozen=True)
class VoiceAssetPlan:
    pair_index: int
    utterance_id: UUID
    audio_relative_path: str
    text_sha256: str
    audio_sha256: str
    media_type: str
    file_extension: str
    size_bytes: int
    existing_unchanged: bool = False


@dataclass(frozen=True)
class VoiceAssetImportPreview:
    corpus_count: int
    text_pair_count: int
    audio_pair_count: int
    complete_pair_count: int
    exact_mapped_count: int
    unmatched_count: int
    ambiguous_count: int
    duplicate_count: int
    unreadable_count: int
    planned_create_count: int
    skipped_unchanged_count: int
    conflicts: tuple[str, ...]
    plans: tuple[VoiceAssetPlan, ...]

    @property
    def gate_passed(self) -> bool:
        return not self.conflicts and self.exact_mapped_count == self.text_pair_count


@dataclass(frozen=True)
class VoiceAssetImportReport:
    created_count: int
    skipped_unchanged_count: int
    total_asset_count: int


class VoiceAssetImportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.assets = UtteranceVoiceAssetRepository(db, settings)

    async def preview(
        self,
        *,
        persona_name: str,
        source_dir: Path,
        text_data: bytes,
        expected_pairs: int | None = None,
    ) -> VoiceAssetImportPreview:
        blocks, _source_hash = NumberedCorpusImportService.parse_blocks(text_data)
        conflicts: list[str] = []
        if expected_pairs is not None and len(blocks) != expected_pairs:
            conflicts.append(f"text_pair_count:{len(blocks)}:expected:{expected_pairs}")
        root, source = self._resolve_roots(source_dir, conflicts)
        audio_by_index, unreadable_count = self._inspect_audio(source, conflicts)
        if expected_pairs is not None and len(audio_by_index) != expected_pairs:
            conflicts.append(f"audio_pair_count:{len(audio_by_index)}:expected:{expected_pairs}")
        text_duplicates = [count for count in Counter(blocks).values() if count > 1]

        persona = await self.db.scalar(
            select(Persona).where(Persona.display_name == persona_name).limit(1)
        )
        if persona is None or persona.active_version_id is None:
            raise AppError("persona_not_found", "Published persona not found.")
        version = await self.db.get(PersonaVersion, persona.active_version_id)
        if version is None or version.status != "published":
            raise AppError("source_not_published", "Active persona version is not published.")
        utterances = list(
            await self.db.scalars(
                select(Utterance).where(
                    Utterance.persona_version_id == version.id,
                    Utterance.verified.is_(True),
                )
            )
        )
        by_reference: dict[str, list[Utterance]] = defaultdict(list)
        for utterance in utterances:
            by_reference[utterance.source_reference].append(utterance)

        plans: list[VoiceAssetPlan] = []
        unmatched = 0
        ambiguous = 0
        duplicate_audio_hashes: Counter[str] = Counter()
        for index, block in enumerate(blocks, start=1):
            reference = NumberedCorpusImportService.reference_for(index)
            candidates = by_reference.get(reference, [])
            audio = audio_by_index.get(index)
            if len(candidates) == 0 or audio is None:
                unmatched += 1
                conflicts.append(f"unmatched_pair:{index:03d}")
                continue
            if len(candidates) > 1:
                ambiguous += 1
                conflicts.append(f"ambiguous_utterance:{index:03d}")
                continue
            utterance = candidates[0]
            if utterance.text != block:
                unmatched += 1
                conflicts.append(f"text_mismatch:{index:03d}")
                continue
            try:
                relative = audio.relative_to(root).as_posix()
            except ValueError:
                conflicts.append(f"audio_outside_root:{index:03d}")
                continue
            audio_hash = self._sha256_file(audio)
            duplicate_audio_hashes[audio_hash] += 1
            media_type = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
            plan = VoiceAssetPlan(
                pair_index=index,
                utterance_id=utterance.id,
                audio_relative_path=relative,
                text_sha256=self._sha256_text(utterance.text),
                audio_sha256=audio_hash,
                media_type=media_type,
                file_extension=audio.suffix.casefold(),
                size_bytes=audio.stat().st_size,
            )
            plans.append(await self._mark_existing(plan, conflicts))

        duplicate_audio_groups = sum(1 for count in duplicate_audio_hashes.values() if count > 1)
        if duplicate_audio_groups:
            conflicts.append(f"duplicate_audio_groups:{duplicate_audio_groups}")
        existing_paths = Counter(plan.audio_relative_path for plan in plans)
        duplicate_path_groups = sum(1 for count in existing_paths.values() if count > 1)
        if duplicate_path_groups:
            conflicts.append(f"duplicate_audio_paths:{duplicate_path_groups}")
        exact_mapped = len(plans)
        skipped = sum(plan.existing_unchanged for plan in plans)
        duplicate_count = len(text_duplicates) + duplicate_audio_groups + duplicate_path_groups
        return VoiceAssetImportPreview(
            corpus_count=len(utterances),
            text_pair_count=len(blocks),
            audio_pair_count=len(audio_by_index),
            complete_pair_count=min(len(blocks), len(audio_by_index)),
            exact_mapped_count=exact_mapped,
            unmatched_count=unmatched,
            ambiguous_count=ambiguous,
            duplicate_count=duplicate_count,
            unreadable_count=unreadable_count,
            planned_create_count=len(plans) - skipped,
            skipped_unchanged_count=skipped,
            conflicts=tuple(dict.fromkeys(conflicts)),
            plans=tuple(plans),
        )

    async def apply(
        self,
        *,
        persona_name: str,
        source_dir: Path,
        text_data: bytes,
        expected_pairs: int | None = None,
    ) -> VoiceAssetImportReport:
        try:
            preview = await self.preview(
                persona_name=persona_name,
                source_dir=source_dir,
                text_data=text_data,
                expected_pairs=expected_pairs,
            )
            if not preview.gate_passed:
                raise AppError(
                    "voice_asset_import_gate_failed",
                    "Voice asset import conflicts must be resolved before apply.",
                )
            created = 0
            for plan in preview.plans:
                if plan.existing_unchanged:
                    continue
                await self.assets.add(
                    UtteranceVoiceAsset(
                        utterance_id=plan.utterance_id,
                        audio_relative_path=plan.audio_relative_path,
                        text_sha256=plan.text_sha256,
                        audio_sha256=plan.audio_sha256,
                        media_type=plan.media_type,
                        file_extension=plan.file_extension,
                        size_bytes=plan.size_bytes,
                    )
                )
                created += 1
            await self.db.commit()
            total = int(
                await self.db.scalar(select(func.count()).select_from(UtteranceVoiceAsset)) or 0
            )
            return VoiceAssetImportReport(
                created_count=created,
                skipped_unchanged_count=preview.skipped_unchanged_count,
                total_asset_count=total,
            )
        except Exception:
            await self.db.rollback()
            raise

    async def _mark_existing(self, plan: VoiceAssetPlan, conflicts: list[str]) -> VoiceAssetPlan:
        existing = await self.assets.get_by_utterance_id(plan.utterance_id)
        path_owner = await self.assets.get_by_relative_path(plan.audio_relative_path)
        if path_owner is not None and path_owner.utterance_id != plan.utterance_id:
            conflicts.append(f"audio_path_already_bound:{plan.pair_index:03d}")
            return plan
        if existing is None:
            return plan
        expected = (
            existing.audio_relative_path,
            existing.text_sha256,
            existing.audio_sha256,
            existing.media_type,
            existing.file_extension,
            existing.size_bytes,
        )
        actual = (
            plan.audio_relative_path,
            plan.text_sha256,
            plan.audio_sha256,
            plan.media_type,
            plan.file_extension,
            plan.size_bytes,
        )
        if expected != actual:
            conflicts.append(f"existing_asset_hash_conflict:{plan.pair_index:03d}")
            return plan
        return replace(plan, existing_unchanged=True)

    def _resolve_roots(self, source_dir: Path, conflicts: list[str]) -> tuple[Path, Path]:
        try:
            source = source_dir.expanduser().resolve(strict=True)
        except OSError as exc:
            raise AppError(
                "source_directory_missing", "Voice source directory is missing."
            ) from exc
        if not source.is_dir():
            raise AppError("source_directory_invalid", "Voice source path is not a directory.")
        configured = self.settings.voice_asset_root or source
        try:
            root = configured.expanduser().resolve(strict=True)
            source.relative_to(root)
        except (OSError, ValueError) as exc:
            conflicts.append("source_outside_voice_asset_root")
            raise AppError(
                "source_outside_voice_asset_root",
                "Voice source directory must be inside VOICE_ASSET_ROOT.",
            ) from exc
        return root, source

    def _inspect_audio(self, source: Path, conflicts: list[str]) -> tuple[dict[int, Path], int]:
        result: dict[int, Path] = {}
        unreadable = 0
        for path in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file():
                continue
            extension = path.suffix.casefold()
            if extension not in SUPPORTED_AUDIO_EXTENSIONS:
                conflicts.append(f"unsupported_file:{path.name}")
                continue
            match = re.fullmatch(r"(\d{3})", path.stem)
            if match is None:
                conflicts.append(f"invalid_audio_name:{path.name}")
                continue
            index = int(match.group(1))
            if index in result:
                conflicts.append(f"duplicate_audio_index:{index:03d}")
                continue
            try:
                size = path.stat().st_size
                with path.open("rb") as handle:
                    header = handle.read(16)
            except OSError:
                unreadable += 1
                conflicts.append(f"unreadable_audio:{index:03d}")
                continue
            if size <= 0:
                conflicts.append(f"empty_audio:{index:03d}")
                continue
            if not self._valid_signature(extension, header):
                conflicts.append(f"invalid_audio_signature:{index:03d}")
                continue
            result[index] = path
        return result, unreadable

    @staticmethod
    def _valid_signature(extension: str, header: bytes) -> bool:
        if extension == ".mp3":
            return header.startswith(b"ID3") or (
                len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
            )
        if extension == ".wav":
            return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
        if extension == ".ogg":
            return header.startswith(b"OggS")
        if extension == ".flac":
            return header.startswith(b"fLaC")
        if extension == ".m4a":
            return len(header) >= 8 and header[4:8] == b"ftyp"
        return False

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
