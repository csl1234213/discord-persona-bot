import re
from dataclasses import dataclass

from app.schemas.chat import SelectedUtterance
from app.services.political_topic_router import PoliticalDecision, PoliticalTopicRouter


@dataclass(frozen=True)
class DirectUtteranceDecision:
    selected: SelectedUtterance | None
    reason_codes: tuple[str, ...]


class DirectUtteranceGate:
    """Conservative exact-or-multi-signal gate; selector scores only rank candidates."""

    RECALL_MARKERS = ("原话", "原句", "重复", "再讲", "讲返", "这句话", "呢句话")

    def __init__(self, router: PoliticalTopicRouter) -> None:
        self.router = router

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", PoliticalTopicRouter._normalize(text))

    @staticmethod
    def _near_embedded(candidate: str, message: str, max_missing: int = 1) -> bool:
        """Allow a near-complete utterance with a tiny omitted typo/character."""
        if len(candidate) < 8:
            return False
        position = 0
        missing = 0
        for character in candidate:
            found = message.find(character, position)
            if found < 0:
                missing += 1
                if missing > max_missing:
                    return False
                continue
            position = found + 1
        return True

    @staticmethod
    def _substantial_overlap(candidate: str, message: str) -> bool:
        """Accept a continuous phrase covering at least five percent of text."""
        if len(candidate) < 2:
            return False
        longest = 0
        for start in range(len(candidate)):
            for end in range(start + 2, len(candidate) + 1):
                if candidate[start:end] in message:
                    longest = max(longest, end - start)
        return longest >= 2 and longest / len(candidate) >= 0.05

    def decide(
        self,
        message: str,
        candidates: list[SelectedUtterance],
        route: PoliticalDecision,
    ) -> DirectUtteranceDecision:
        if not candidates:
            return DirectUtteranceDecision(None, ("DIRECT_REJECT_NO_CANDIDATE",))
        top = candidates[0]
        candidate_route = self.router.route(top.text)
        if route.is_political != candidate_route.is_political:
            return DirectUtteranceDecision(None, ("DIRECT_REJECT_TOPIC_MISMATCH",))
        if route.is_political and not set(route.concept_codes).intersection(
            candidate_route.concept_codes
        ):
            return DirectUtteranceDecision(None, ("DIRECT_REJECT_POLITICAL_CONCEPT_MISMATCH",))

        message_compact = self._compact(message)
        candidate_compact = self._compact(top.text)
        if message_compact and message_compact == candidate_compact:
            return DirectUtteranceDecision(
                top, ("DIRECT_NORMALIZED_EXACT", "DIRECT_TOPIC_COMPATIBLE")
            )

        if any(marker in message_compact for marker in self.RECALL_MARKERS):
            quoted = re.findall(r"[“\"']([^”\"']{4,})[”\"']", message)
            if len(quoted) == 1 and self._compact(quoted[0]) in candidate_compact:
                return DirectUtteranceDecision(
                    top, ("DIRECT_EXPLICIT_RECALL", "DIRECT_TOPIC_COMPATIBLE")
                )

        # A short keyword or fragment is not enough to replay a full utterance.
        # Only replay when the complete canonical sentence is present in the
        # user's normalized message (possibly surrounded by extra text).
        embedded = [
            candidate
            for candidate in candidates
            if self._near_embedded(self._compact(candidate.text), message_compact)
            or self._substantial_overlap(self._compact(candidate.text), message_compact)
        ]
        if embedded:
            lengths = [len(self._compact(candidate.text)) for candidate in embedded]
            if len(embedded) > 1 and len(set(lengths)) == 1:
                return DirectUtteranceDecision(None, ("DIRECT_REJECT_AMBIGUOUS",))
            selected = max(embedded, key=lambda candidate: len(self._compact(candidate.text)))
            return DirectUtteranceDecision(
                selected,
                (
                    "DIRECT_FULL_SENTENCE_EMBEDDED",
                    "DIRECT_TOPIC_COMPATIBLE",
                ),
            )
        return DirectUtteranceDecision(None, ("DIRECT_REJECT_AMBIGUOUS",))
