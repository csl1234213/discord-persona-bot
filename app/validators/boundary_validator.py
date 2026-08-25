import re
from dataclasses import dataclass

from app.db.models import CatchphraseRule, VocabularyRule


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason_codes: list[str]


class BoundaryValidator:
    IDENTITY_PATTERNS = (
        re.compile(r"(?:我就是|本人就是|我正是)"),
        re.compile(r"I\s+am\s+the\s+real\s+person", re.IGNORECASE),
    )
    FABRICATED_QUOTE = re.compile(r"(?:他说|她说|本人说|[Tt]hey said)\s*[：:]\s*[“\"]")
    INTERNAL_PATTERN = re.compile(
        r"(?:system prompt|系统提示词|internal instruction|内部指令)", re.IGNORECASE
    )

    def validate(
        self,
        answer: str,
        *,
        persona_name: str,
        vocabulary_rules: list[VocabularyRule],
        catchphrase_rules: list[CatchphraseRule],
        conversation_text: str = "",
    ) -> ValidationResult:
        reasons: list[str] = []
        if not answer.strip():
            reasons.append("empty_answer")
        if any(pattern.search(answer) for pattern in self.IDENTITY_PATTERNS):
            reasons.append("real_person_identity_claim")
        if persona_name and re.search(rf"我(?:就)?是\s*{re.escape(persona_name)}", answer):
            reasons.append("named_identity_claim")
        if self.FABRICATED_QUOTE.search(answer):
            reasons.append("fabricated_attributed_quote")
        if self.INTERNAL_PATTERN.search(answer):
            reasons.append("internal_instruction_leak")
        if len(answer) > 8000:
            reasons.append("output_too_long")
        for rule in vocabulary_rules:
            if (
                rule.enabled
                and rule.rule_type == "avoided"
                and rule.weight >= 1
                and rule.text in answer
            ):
                reasons.append(f"hard_avoided_vocabulary:{rule.id}")
        for rule in catchphrase_rules:
            if rule.enabled and answer.count(rule.text) > rule.max_per_response:
                reasons.append(f"catchphrase_limit:{rule.id}")
            if (
                rule.enabled
                and conversation_text.count(rule.text) + answer.count(rule.text)
                > rule.max_per_conversation
            ):
                reasons.append(f"conversation_catchphrase_limit:{rule.id}")
        return ValidationResult(valid=not reasons, reason_codes=reasons)
