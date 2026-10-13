import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker
from app.services.numbered_corpus_import_service import NumberedCorpusImportService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a published persona version from blank-line numbered text blocks."
    )
    parser.add_argument("source_file", type=Path)
    parser.add_argument("--persona-name", default="刁民")
    parser.add_argument("--target-version", default="0.6.0")
    parser.add_argument("--expected-blocks", type=int, default=70)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def json_payload(value) -> str:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if key.endswith("_id"):
            payload[key] = str(item)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            grant_local_admin(session)
            service = NumberedCorpusImportService(session)
            data = args.source_file.read_bytes()
            if args.apply:
                report = await service.apply(
                    persona_name=args.persona_name,
                    target_version=args.target_version,
                    data=data,
                    expected_blocks=args.expected_blocks,
                )
                print(json_payload(report))
                return
            preview = await service.preview(
                persona_name=args.persona_name,
                target_version=args.target_version,
                data=data,
                expected_blocks=args.expected_blocks,
            )
            print(json_payload(preview))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
