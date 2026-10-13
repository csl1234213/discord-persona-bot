import re
from dataclasses import dataclass

from app.db.models import CatchphraseRule, VocabularyRule
from app.services.political_topic_router import PoliticalDecision, PoliticalTopicRouter


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
    AI_IDENTITY_PATTERN = re.compile(
        r"(?:我是\s*(?:AI|人工智能|智能助手|语言模型|chatbot|assistant|机器人)|"
        r"我係\s*(?:AI|人工智能|智能助手|語言模型|chatbot|assistant|機械人)|"
        r"我(?:不|唔)係真人|我(?:没有|冇)(?:身体|情绪)|"
        r"\b(?:as an ai|i am an ai|language model|chatbot|assistant)\b)",
        re.IGNORECASE,
    )
    INTERNAL_PATTERN = re.compile(
        r"(?:system prompt|developer message|系统提示词|內部指令|内部指令|"
        r"corrective_instruction|selected_utterances|utterance[_ ]?id|"
        r"response_origin|abrasive_style_descriptor)",
        re.IGNORECASE,
    )
    PERSONAL_FACT_QUERY_PATTERN = re.compile(
        r"(?:(?:你|您).{0,8}(?:兄弟|姐妹|家人|父母|住址|地址|住喺|住在|收入|"
        r"工资|人工|学校|讀書|读书|公司|疾病|病史|旅行经历|现实朋友|行程|"
        r"经历|經歷|童年|出生|结婚|結婚)|"
        r"(?:兄弟|姐妹|住址|地址|收入|工资|人工|学校|公司|疾病|病史|行程|"
        r"经历|經歷|童年|出生).{0,8}(?:有冇|有没有|几多|多少|边度|哪里|吗|係點|系点))"
    )
    SAFE_ABSTENTION_PATTERN = re.compile(
        r"(?:唔知|不知道|边知|邊知|冇讲过|冇講過|不确定|唔确定|唔確定|无可奉告)"
    )
    ABRASIVE_TERMS = (
        "蠢",
        "笨蛋",
        "傻仔",
        "戆鸠",
        "戇鳩",
        "仆街",
        "收皮",
        "垃圾",
        "废物",
        "廢物",
        "扑街",
    )
    STRONG_ABRASIVE_TERMS = ("戆鸠", "戇鳩", "仆街", "扑街", "废物", "廢物")
    THREAT_PATTERN = re.compile(r"(?:杀死|殺死|打死|弄死|斩|斬|起底|人肉|追杀|追殺)")
    HARASSMENT_PATTERN = re.compile(r"(?:一齐围攻|一齊圍攻|去骚扰|去騷擾|不断辱骂|不斷辱罵)")
    PROTECTED_ATTACK_PATTERN = re.compile(
        r"(?:种族|種族|民族|宗教|性别|性別|残疾|殘疾|同性恋|同性戀).{0,8}"
        r"(?:垃圾|低等|滚|滾|该死|該死)"
    )

    def validate(
        self,
        answer: str,
        *,
        persona_name: str,
        vocabulary_rules: list[VocabularyRule],
        catchphrase_rules: list[CatchphraseRule],
        conversation_text: str = "",
        user_message: str = "",
        response_origin: str = "CORPUS_COMPOSED",
        political_decision: PoliticalDecision | None = None,
        abrasive_intensity: str = "NORMAL",
        abrasive_trigger_detected: bool = False,
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
        if self.AI_IDENTITY_PATTERN.search(answer):
            reasons.append("AI_IDENTITY_LEAK")
        if self.INTERNAL_PATTERN.search(answer):
            reasons.append("INTERNAL_TERM_LEAK")
        if len(answer) > 8000:
            reasons.append("output_too_long")
        personal_fact_query = self.PERSONAL_FACT_QUERY_PATTERN.search(user_message)
        safe_abstention = self.SAFE_ABSTENTION_PATTERN.search(answer)
        if personal_fact_query and not safe_abstention:
            reasons.append("UNSUPPORTED_FIRST_PERSON_FACT")
        if response_origin == "CORPUS_COMPOSED":
            answer_route = PoliticalTopicRouter().route(answer)
            if answer_route.is_political:
                reasons.append("POLITICAL_CONTENT_IN_COMPOSED")
            if political_decision is not None and political_decision.is_political:
                reasons.append("POLITICAL_ROUTE_IN_COMPOSED")
        abrasive_count = sum(answer.count(term) for term in self.ABRASIVE_TERMS)
        if abrasive_count > 1:
            reasons.append("ABRASIVE_POLICY_VIOLATION")
        if abrasive_intensity == "NORMAL" and abrasive_count:
            reasons.append("ABRASIVE_POLICY_VIOLATION")
        if abrasive_intensity == "TEASING" and any(
            term in answer for term in self.STRONG_ABRASIVE_TERMS
        ):
            reasons.append("ABRASIVE_POLICY_VIOLATION")
        if abrasive_intensity == "ABRASIVE" and abrasive_count and not abrasive_trigger_detected:
            reasons.append("ABRASIVE_POLICY_VIOLATION")
        if (
            self.THREAT_PATTERN.search(answer)
            or self.HARASSMENT_PATTERN.search(answer)
            or self.PROTECTED_ATTACK_PATTERN.search(answer)
        ):
            reasons.append("ABRASIVE_POLICY_VIOLATION")
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
        unique_reasons = list(dict.fromkeys(reasons))
        return ValidationResult(valid=not unique_reasons, reason_codes=unique_reasons)
