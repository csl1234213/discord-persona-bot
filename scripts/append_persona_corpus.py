import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker
from app.services.persona_corpus_append_service import PersonaCorpusAppendService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone the active persona version, append unique UTF-8 lines, and publish."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--persona-name", default="刁民")
    parser.add_argument("--target-version", default="0.4.0")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            grant_local_admin(session)
            report = await PersonaCorpusAppendService(session, settings).append_text_file(
                persona_name=args.persona_name,
                target_version=args.target_version,
                filename=args.path.name,
                data=args.path.read_bytes(),
            )
            payload = asdict(report)
            for key in ("persona_id", "source_version_id", "target_version_id"):
                payload[key] = str(payload[key])
            print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
