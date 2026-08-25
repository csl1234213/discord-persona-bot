import argparse
import asyncio
import json
from dataclasses import asdict

from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker
from app.services.persona_corpus_replace_service import PersonaCorpusReplaceService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone the active persona version, replace every matching utterance, "
            "and deduplicate the replacement."
        )
    )
    parser.add_argument("--persona-name", default="刁民")
    parser.add_argument("--target-version", default="0.5.0")
    parser.add_argument("--remove-fragment", required=True)
    parser.add_argument("--replacement-text", required=True)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            report = await PersonaCorpusReplaceService(session).replace_utterance_containing(
                persona_name=args.persona_name,
                target_version=args.target_version,
                remove_fragment=args.remove_fragment,
                replacement_text=args.replacement_text,
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
