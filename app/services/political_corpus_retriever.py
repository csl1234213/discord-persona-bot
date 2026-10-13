from dataclasses import dataclass
from uuid import UUID

from app.schemas.chat import SelectedUtterance, SelectionContext
from app.services.political_topic_router import PoliticalDecision, PoliticalTopicRouter
from app.services.utterance_selector import LexicalUtteranceSelector


@dataclass(frozen=True)
class PoliticalRetrievalResult:
    selected: SelectedUtterance | None
    reason_codes: tuple[str, ...]


class PoliticalCorpusRetriever:
    GENERIC_CONCEPTS = frozenset({"policy"})
    CONTEXT_CONCEPT_FAMILIES = {
        "taiwan": (
            frozenset({"unification", "sovereignty", "sovereignty_relation"}),
        ),
    }
    CONTEXT_FAMILY_KEYWORDS = {
        "taiwan": {
            frozenset(
                {"unification", "sovereignty", "sovereignty_relation"}
            ): (
                "台湾",
                "两岸",
                "主权",
                "统一",
            ),
        },
    }

    def __init__(
        self, selector: LexicalUtteranceSelector, router: PoliticalTopicRouter
    ) -> None:
        self.selector = selector
        self.router = router

    @classmethod
    def compatibility_tier(
        cls,
        route: PoliticalDecision,
        candidate_route: PoliticalDecision,
    ) -> str | None:
        if any(
            reason.startswith("NONPOL_COLLISION:")
            for reason in candidate_route.reason_codes
        ):
            return None
        query_specific = set(route.concept_codes) - cls.GENERIC_CONCEPTS
        candidate_specific = (
            set(candidate_route.concept_codes) - cls.GENERIC_CONCEPTS
        )
        if query_specific.intersection(candidate_specific):
            return "EXACT"
        shared_contexts = set(route.context_codes).intersection(
            candidate_route.context_codes
        )
        if (
            set(route.concept_codes) == cls.GENERIC_CONCEPTS
            and set(candidate_route.concept_codes) == cls.GENERIC_CONCEPTS
            and shared_contexts
        ):
            return "EXACT"
        query_family_signals = query_specific.union(route.relation_codes)
        candidate_family_signals = candidate_specific.union(
            candidate_route.relation_codes
        )
        for context in shared_contexts:
            for family in cls.CONTEXT_CONCEPT_FAMILIES.get(context, ()):
                if query_family_signals.intersection(
                    family
                ) and candidate_family_signals.intersection(family):
                    return "CONTEXT_FAMILY"
        return None

    @classmethod
    def expanded_retrieval_message(
        cls,
        message: str,
        route: PoliticalDecision,
    ) -> tuple[str, tuple[str, ...]]:
        query_specific = set(route.concept_codes) - cls.GENERIC_CONCEPTS
        keywords: list[str] = []
        reasons: list[str] = []
        for context in route.context_codes:
            for family, family_keywords in cls.CONTEXT_FAMILY_KEYWORDS.get(
                context, {}
            ).items():
                if query_specific.intersection(family):
                    keywords.extend(family_keywords)
                    reasons.append(
                        f"POLITICAL_CONTEXT_KEYWORD_EXPANSION:{context}"
                    )
        if not keywords:
            return message, ()
        expanded = " ".join((message, *dict.fromkeys(keywords)))
        return expanded, tuple(dict.fromkeys(reasons))

    async def retrieve(
        self,
        *,
        message: str,
        persona_version_id: UUID,
        context: SelectionContext,
        route: PoliticalDecision,
        recent_utterance_ids: list[UUID],
    ) -> PoliticalRetrievalResult:
        retrieval_message, expansion_reasons = self.expanded_retrieval_message(
            message, route
        )
        selection = await self.selector.select(
            message=retrieval_message,
            persona_version_id=persona_version_id,
            context=context,
            limit=10,
            recent_utterance_ids=recent_utterance_ids,
            minimum_score=0.0,
            allow_intent_fallback=False,
        )
        eligible: dict[str, list[SelectedUtterance]] = {
            "EXACT": [],
            "CONTEXT_FAMILY": [],
        }
        for item in selection.items:
            candidate_route = self.router.route(item.text)
            tier = self.compatibility_tier(route, candidate_route)
            if tier is None:
                continue
            if not candidate_route.is_political and tier != "CONTEXT_FAMILY":
                continue
            if route.context_codes and candidate_route.context_codes:
                if not set(route.context_codes).intersection(candidate_route.context_codes):
                    continue
            if item.score < 0.05:
                continue
            if not {"lexical_overlap", "cjk_phrase_coverage"}.intersection(item.reason_codes):
                continue
            eligible[tier].append(item)
        selected_tier = next(
            (tier for tier in ("EXACT", "CONTEXT_FAMILY") if eligible[tier]),
            None,
        )
        if selected_tier is None:
            return PoliticalRetrievalResult(None, ("POLITICAL_RETRIEVAL_NO_HIT",))
        tier_candidates = eligible[selected_tier]
        if len(tier_candidates) > 1 and tier_candidates[0].score == tier_candidates[1].score:
            return PoliticalRetrievalResult(None, ("POLITICAL_RETRIEVAL_AMBIGUOUS",))
        return PoliticalRetrievalResult(
            tier_candidates[0],
            (
                f"POLITICAL_RETRIEVAL_SINGLE_{selected_tier}",
                *expansion_reasons,
                *tier_candidates[0].reason_codes,
            ),
        )
