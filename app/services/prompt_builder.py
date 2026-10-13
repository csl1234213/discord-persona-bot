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
        abrasive_intensity: str = "NORMAL",
        abrasive_style_descriptor: str = "",
    ) -> PersonaGenerationRequest:
        del evidence, catchphrases
        style_data = {
            "sentence_length_preference": style.sentence_length_preference,
            "paragraph_length_preference": style.paragraph_length_preference,
            "rhetorical_question_tendency": style.rhetorical_question_tendency,
            "list_tendency": style.list_tendency,
            "emoji_policy": style.emoji_policy,
            "punctuation_density": "low" if not style.punctuation_preferences else "moderate",
        }
        system_prompt = "\n\n".join(
            [
                STRICT_RULES,
                "SIMULATION DISCLOSURE: " + disclosure,
                "ABSTRACT STYLE PROFILE (form only):\n"
                + json.dumps(style_data, ensure_ascii=False, sort_keys=True),
                "PERSONALITY TONE CONTROLS ONLY:\n"
                + personality.model_dump_json(exclude={"sources"}),
                f"ADDRESS: {resolved_address}\nRELATIONSHIP: {relationship or 'unspecified'}",
                f"STYLE INTENSITY: {abrasive_intensity}\n{abrasive_style_descriptor}",
            ]
        )
        return PersonaGenerationRequest(
            system_prompt=system_prompt,
            user_message=user_message,
            selected_utterances=[],
            history=history,
            resolved_address=resolved_address,
            effective_personality=personality,
            abrasive_intensity=abrasive_intensity,
            abrasive_style_descriptor=abrasive_style_descriptor,
        )
