import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import Settings  # noqa: E402
from app.db.session import build_engine, build_sessionmaker  # noqa: E402
from app.services.voice_asset_import_service import (  # noqa: E402
    VoiceAssetImportService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map numbered text/audio pairs to utterance voice asset metadata."
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--persona-name", default="刁民")
    parser.add_argument("--expected-pairs", type=int, default=70)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def json_payload(value) -> str:
    payload = asdict(value)
    payload.pop("plans", None)
    payload["gate_passed"] = getattr(value, "gate_passed", True)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


async def run(args: argparse.Namespace) -> None:
    settings = Settings(voice_asset_root=args.source_dir)
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            service = VoiceAssetImportService(session, settings)
            kwargs = {
                "persona_name": args.persona_name,
                "source_dir": args.source_dir,
                "text_data": args.text_file.read_bytes(),
                "expected_pairs": args.expected_pairs,
            }
            result = (
                await service.apply(**kwargs) if args.apply else await service.preview(**kwargs)
            )
            print(json_payload(result))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
