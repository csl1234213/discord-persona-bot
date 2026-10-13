import argparse
import asyncio
import json
from dataclasses import asdict

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.session import build_engine, build_sessionmaker
from app.services.persona_version_consolidation_service import (
    PersonaVersionConsolidationService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atomically consolidate persona versions.")
    parser.add_argument("--persona-name", default="刁民")
    parser.add_argument("--source-version", default="0.2.0")
    parser.add_argument("--target-version", default="0.3.0")
    parser.add_argument(
        "--delete-version",
        action="append",
        dest="delete_versions",
        default=["0.1.0", "0.2.0"],
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            grant_local_admin(session)
            report = await PersonaVersionConsolidationService(session).consolidate(
                persona_name=args.persona_name,
                source_version=args.source_version,
                target_version=args.target_version,
                delete_versions=set(args.delete_versions),
            )
            payload = asdict(report)
            payload["persona_id"] = str(report.persona_id)
            payload["target_version_id"] = str(report.target_version_id)
            print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
