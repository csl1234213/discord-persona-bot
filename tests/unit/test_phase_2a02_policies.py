import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.abrasive_style_policy import AbrasiveIntensity, AbrasiveStylePolicy
from app.services.political_corpus_retriever import PoliticalCorpusRetriever
from app.services.political_topic_router import PoliticalDecision, PoliticalTopicRouter
from app.validators.boundary_validator import BoundaryValidator

CASES = json.loads(
    (Path(__file__).parents[1] / "evaluation" / "phase_2a02_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES["router_cases"], ids=lambda case: case["id"])
def test_political_router_confusion_matrix_cases(case):
    decision = PoliticalTopicRouter().route(case["input"])
    assert decision.is_political is case["expected_political"]
    assert decision.reason_codes
    if decision.is_political:
        assert decision.concept_codes
        assert decision.relation_codes


def test_political_router_only_inherits_latest_user_context():
    from app.schemas.chat import HistoryMessage

    router = PoliticalTopicRouter()
    history = [
        HistoryMessage(role="user", content="香港国安法你点睇？"),
        HistoryMessage(role="assistant", content="天气几好。"),
    ]
    inherited = router.route("咁你支持呢件事吗？", history)
    assert inherited.is_political
    assert "POL_PRIOR_USER_CONTEXT" in inherited.reason_codes

    assistant_only = [HistoryMessage(role="assistant", content="香港国安法你点睇？")]
    assert not router.route("咁你支持呢件事吗？", assistant_only).is_political


@pytest.mark.parametrize(
    ("query_concepts", "query_contexts", "candidate_concepts", "candidate_contexts", "tier"),
    [
        (("unification",), ("taiwan",), ("sovereignty",), ("taiwan",), "CONTEXT_FAMILY"),
        (("sovereignty",), ("taiwan",), ("unification",), ("taiwan",), "CONTEXT_FAMILY"),
        (("unification",), (), ("sovereignty",), ("taiwan",), None),
        (("unification",), ("hong_kong",), ("sovereignty",), ("taiwan",), None),
        (("unification",), ("taiwan",), ("election",), ("taiwan",), None),
        (("policy", "unification"), ("taiwan",), ("policy", "election"), ("taiwan",), None),
        (("unification",), ("taiwan",), (), ("taiwan",), "CONTEXT_FAMILY"),
        (("national_security",), ("hong_kong",), ("national_security",), (), "EXACT"),
        (("policy",), ("united_kingdom",), ("policy",), ("united_kingdom",), "EXACT"),
        (("policy",), (), ("policy",), (), None),
    ],
)
def test_political_retrieval_compatibility_is_context_scoped(
    query_concepts,
    query_contexts,
    candidate_concepts,
    candidate_contexts,
    tier,
):
    query = PoliticalDecision(
        True,
        ("query",),
        concept_codes=query_concepts,
        context_codes=query_contexts,
    )
    candidate = PoliticalDecision(
        candidate_concepts != (),
        ("candidate",),
        concept_codes=candidate_concepts,
        relation_codes=("sovereignty_relation",)
        if not candidate_concepts and candidate_contexts == ("taiwan",)
        else (),
        context_codes=candidate_contexts,
    )
    assert PoliticalCorpusRetriever.compatibility_tier(query, candidate) == tier


def test_political_retrieval_expands_only_reviewed_taiwan_concept_family():
    query = PoliticalDecision(
        True,
        ("query",),
        concept_codes=("policy", "unification"),
        context_codes=("taiwan",),
    )
    expanded, reasons = PoliticalCorpusRetriever.expanded_retrieval_message(
        "两岸统一政策争议你点睇？", query
    )
    assert "台湾" in expanded
    assert "主权" in expanded
    assert reasons == ("POLITICAL_CONTEXT_KEYWORD_EXPANSION:taiwan",)

    unrelated = PoliticalDecision(
        True,
        ("query",),
        concept_codes=("election",),
        context_codes=("taiwan",),
    )
    original, reasons = PoliticalCorpusRetriever.expanded_retrieval_message(
        "台湾选举点睇？", unrelated
    )
    assert original == "台湾选举点睇？"
    assert reasons == ()


def test_abrasive_policy_is_seeded_configurable_and_feature_off():
    settings = Settings(
        persona_abrasive_style_enabled=True,
        persona_abrasive_normal_weight=70,
        persona_abrasive_teasing_weight=20,
        persona_abrasive_abrasive_weight=10,
    )
    policy = AbrasiveStylePolicy(settings)
    first = policy.choose(
        conversation_id="conversation", turn_key="turn", user_message="返工好烦"
    )
    second = policy.choose(
        conversation_id="conversation", turn_key="turn", user_message="返工好烦"
    )
    assert first == second

    disabled = AbrasiveStylePolicy(Settings(persona_abrasive_style_enabled=False)).choose(
        conversation_id="conversation",
        turn_key="turn",
        user_message="返工好烦",
        forced_intensity=AbrasiveIntensity.ABRASIVE,
    )
    assert disabled.intensity == AbrasiveIntensity.NORMAL


def test_abrasive_policy_recognizes_traditional_cantonese_trigger():
    decision = AbrasiveStylePolicy(Settings()).choose(
        conversation_id="conversation",
        turn_key="traditional-trigger",
        user_message="返工好煩",
        forced_intensity=AbrasiveIntensity.ABRASIVE,
    )
    assert decision.trigger_detected is True
    assert decision.intensity == AbrasiveIntensity.ABRASIVE


@pytest.mark.parametrize("case", CASES["abrasive_cases"], ids=lambda case: case["id"])
def test_abrasive_regression_cases_have_no_political_contamination(case):
    intensity = AbrasiveIntensity(case["intensity"])
    decision = AbrasiveStylePolicy(Settings()).choose(
        conversation_id="evaluation",
        turn_key=case["id"],
        user_message=case["input"],
        forced_intensity=intensity,
    )
    if intensity == AbrasiveIntensity.ABRASIVE:
        assert decision.trigger_detected
    validation = BoundaryValidator().validate(
        case["output"],
        persona_name="测试人物",
        vocabulary_rules=[],
        catchphrase_rules=[],
        user_message=case["input"],
        response_origin="CORPUS_COMPOSED",
        political_decision=PoliticalTopicRouter().route(case["input"]),
        abrasive_intensity=decision.intensity.value,
        abrasive_trigger_detected=decision.trigger_detected,
    )
    assert validation.valid, validation.reason_codes
    assert not PoliticalTopicRouter().route(case["output"]).is_political


@pytest.mark.parametrize(
    ("user_message", "answer", "expected_reason"),
    [
        ("早晨", "我是 AI，所以没有情绪。", "AI_IDENTITY_LEAK"),
        ("你有兄弟姐妹吗？", "我有一个弟弟。", "UNSUPPORTED_FIRST_PERSON_FACT"),
        ("早晨", "香港国安法我支持。", "POLITICAL_CONTENT_IN_COMPOSED"),
        ("早晨", "selected_utterances 已加载。", "INTERNAL_TERM_LEAK"),
        ("返工好烦", "仆街，垃圾，做嘢啦。", "ABRASIVE_POLICY_VIOLATION"),
    ],
)
def test_composed_validator_phase_2a02_reason_codes(user_message, answer, expected_reason):
    result = BoundaryValidator().validate(
        answer,
        persona_name="测试人物",
        vocabulary_rules=[],
        catchphrase_rules=[],
        user_message=user_message,
        response_origin="CORPUS_COMPOSED",
        political_decision=PoliticalTopicRouter().route(user_message),
        abrasive_intensity="ABRASIVE",
        abrasive_trigger_detected=True,
    )
    assert expected_reason in result.reason_codes


@pytest.mark.parametrize("query", ["兄弟姐妹", "收入", "住址"])
def test_persona_safe_abstention_does_not_use_ai_identity(query):
    result = BoundaryValidator().validate(
        "唔知喎，冇讲过。",
        persona_name="测试人物",
        vocabulary_rules=[],
        catchphrase_rules=[],
        user_message=f"你嘅{query}系点？",
        response_origin="CORPUS_COMPOSED",
    )
    assert result.valid


def test_abrasive_weights_must_have_positive_total():
    with pytest.raises(ValueError):
        Settings(
            persona_abrasive_normal_weight=0,
            persona_abrasive_teasing_weight=0,
            persona_abrasive_abrasive_weight=0,
        )
