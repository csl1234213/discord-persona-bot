import argparse
import asyncio

from sqlalchemy import select

from app.core.authorization import grant_local_admin
from app.core.config import Settings
from app.db.models import Persona
from app.db.session import build_engine, build_sessionmaker, init_database
from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.persona_update_service import PersonaUpdateService

DEMO_UTTERANCES = (
    "先把已知事实说清楚，再讨论可能的判断。",
    "如果材料不足，我宁愿明确说不知道，也不补写不存在的经历。",
    "这个结论只代表现有资料能支持的范围。",
)


async def seed(name: str, publish: bool) -> None:
    settings = Settings()
    settings.ensure_local_directories()
    engine = build_engine(settings.database_url)
    try:
        await init_database(engine)
        sessionmaker = build_sessionmaker(engine)
        async with sessionmaker() as db:
            grant_local_admin(db)
            existing = await db.scalar(select(Persona).where(Persona.display_name == name))
            if existing:
                print(f"Demo persona already exists: {existing.id}")
                return
            service = PersonaUpdateService(db)
            persona, version = await service.create_persona(
                PersonaCreate(
                    display_name=name,
                    description="仅用于本地 fake-provider 冒烟验证的授权演示人物。",
                )
            )
            for text in DEMO_UTTERANCES:
                await service.add_utterance(
                    persona.id,
                    version.id,
                    UtteranceCreate(
                        text=text,
                        source_type="demo_authorized",
                        source_reference="scripts/seed_demo_persona.py",
                        verified=True,
                    ),
                )
            if publish:
                await service.publish(persona.id, version.id)
            print(
                f"Seeded persona={persona.id} version={version.id} "
                f"status={'published' if publish else 'draft'}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a local, non-production demo persona.")
    parser.add_argument("--name", default="严格证据演示人物")
    parser.add_argument("--draft-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(seed(args.name, not args.draft_only))


if __name__ == "__main__":
    main()
