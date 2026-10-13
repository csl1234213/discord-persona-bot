from __future__ import annotations

import json
from pathlib import Path

from app.schemas.persona import PersonaCreate, UtteranceCreate
from app.services.persona_update_service import PersonaUpdateService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_CASES_PATH = (
    PROJECT_ROOT / "tests" / "evaluation" / "phase_2a02_cases.json"
)
POLITICAL_CORPUS = {
    "hk_law": "我反对香港国安法。",
    "tw_sovereignty": "台湾主权问题要讲清楚。",
    "party_election": "我支持这个政党参加选举。",
    "protest": "示威事件应该和平处理。",
    "system": "一党制政治制度有问题。",
}


def load_political_policy_cases() -> dict:
    return json.loads(POLICY_CASES_PATH.read_text(encoding="utf-8"))


async def seed_political_corpus(db):
    update = PersonaUpdateService(db)
    persona, version = await update.create_persona(
        PersonaCreate(display_name="测试人物", coverage_threshold=0.05)
    )
    utterances = {}
    for key, text in POLITICAL_CORPUS.items():
        utterances[key] = await update.add_utterance(
            persona.id,
            version.id,
            UtteranceCreate(text=text, verified=True),
        )
    await update.publish(persona.id, version.id)
    return utterances
