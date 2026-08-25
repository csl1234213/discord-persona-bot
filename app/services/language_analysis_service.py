import re
from collections import Counter
from statistics import mean

from app.db.models import Utterance
from app.schemas.persona import LanguageAnalysis


class LanguageAnalysisService:
    PUNCTUATION = "，。！？；：、…,.!?;:"
    TRANSITIONS = ("但是", "所以", "不过", "然后", "其实", "因此", "另外", "總之", "所以說")
    ADDRESS_PATTERN = re.compile(r"(?:^|[，。！？\s])([\u4e00-\u9fff]{1,4})(?:，|：|:)")

    def analyze(self, utterances: list[Utterance]) -> LanguageAnalysis:
        texts = [item.text.strip() for item in utterances if item.verified and item.text.strip()]
        characters = Counter(
            char for text in texts for char in text if "\u4e00" <= char <= "\u9fff"
        )
        phrases: Counter[str] = Counter()
        for text in texts:
            compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
            for length in range(2, 6):
                phrases.update(
                    compact[index : index + length] for index in range(len(compact) - length + 1)
                )
        punctuation = {mark: sum(text.count(mark) for text in texts) for mark in self.PUNCTUATION}
        punctuation = {mark: count for mark, count in punctuation.items() if count}
        openings = Counter(text[: min(4, len(text))] for text in texts if text)
        transitions = Counter(
            transition for text in texts for transition in self.TRANSITIONS if transition in text
        )
        addresses = Counter(
            match.group(1) for text in texts for match in self.ADDRESS_PATTERN.finditer(text)
        )
        lengths = [len(text) for text in texts]
        frequent_phrases = [
            {"text": text, "count": count} for text, count in phrases.most_common(20) if count >= 2
        ]
        return LanguageAnalysis(
            frequent_words=[
                {"text": text, "count": count} for text, count in characters.most_common(20)
            ],
            frequent_phrases=frequent_phrases,
            candidate_catchphrases=frequent_phrases[:8],
            sentence_length={
                "count": len(lengths),
                "minimum": min(lengths, default=0),
                "maximum": max(lengths, default=0),
                "average": round(mean(lengths), 2) if lengths else 0,
            },
            punctuation_frequencies=punctuation,
            common_openings=[
                {"text": text, "count": count} for text, count in openings.most_common(8)
            ],
            common_transitions=[
                {"text": text, "count": count} for text, count in transitions.most_common()
            ],
            addresses=[{"text": text, "count": count} for text, count in addresses.most_common(8)],
            published=False,
        )
