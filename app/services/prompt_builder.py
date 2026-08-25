import json

from app.db.models import CatchphraseRule, StyleProfile
from app.prompts.persona_system_prompt import STRICT_RULES
from app.schemas.chat import (
    HistoryMessage,
    PersonaGenerationRequest,
    ResolvedPersonality,
    SelectedUtterance,
)


class PromptBuilder:
    def build(
        self,
        *,
        disclosure: str,
        style: StyleProfile,
        personality: ResolvedPersonality,
        resolved_address: str,
        relationship: str | None,
        evidence: list[SelectedUtterance],
        history: list[HistoryMessage],
        user_message: str,
        catchphrases: list[CatchphraseRule],
    ) -> PersonaGenerationRequest:
        style_data = {
            column.name: getattr(style, column.name)
            for column in StyleProfile.__table__.columns
            if column.name != "persona_version_id"
        }
        catchphrase_data = [
            {
                "text": item.text,
                "probability": item.probability,
                "max_per_response": item.max_per_response,
                "max_per_conversation": item.max_per_conversation,
                "allowed_scenes": item.allowed_scenes,
            }
            for item in catchphrases
            if item.enabled
        ]
        system_prompt = "\n\n".join(
            [
                f"DISCLOSURE:\n{disclosure}",
                STRICT_RULES,
                f"STYLE PROFILE:\n{json.dumps(style_data, ensure_ascii=False, sort_keys=True)}",
                "PERSONALITY TONE CONTROLS ONLY:\n"
                + personality.model_dump_json(exclude={"sources"}),
                f"ADDRESS: {resolved_address}\nRELATIONSHIP: {relationship or 'unspecified'}",
                "VERIFIED EVIDENCE:\n"
                + json.dumps(
                    [item.model_dump(mode="json") for item in evidence], ensure_ascii=False
                ),
                f"CATCHPHRASE RULES:\n{json.dumps(catchphrase_data, ensure_ascii=False)}",
                "Answer only when the evidence supports the user's topic.",
            ]
        )
        return PersonaGenerationRequest(
            system_prompt=system_prompt,
            user_message=user_message,
            selected_utterances=evidence,
            history=history,
            resolved_address=resolved_address,
            effective_personality=personality,
        )
