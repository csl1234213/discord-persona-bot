import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from math import log
from random import SystemRandom
from typing import Protocol
from unicodedata import normalize
from uuid import UUID

from app.db.models import Utterance
from app.repositories.utterance_repository import UtteranceRepository
from app.schemas.chat import SelectedUtterance, SelectionContext, SelectionResult

CJK_SEQUENCE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
LATIN_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
ALPHANUMERIC_COMPONENT_PATTERN = re.compile(r"[a-z]+|[0-9]+")
LOW_INFORMATION_CJK_TOKENS = {
    "一个",
    "不是",
    "什么",
    "他们",
    "你们",
    "但是",
    "因为",
    "已经",
    "我们",
    "所以",
    "怎么",
    "怎样",
    "这个",
    "那个",
    "还是",
    "然后",
    "现在",
    "就是",
    "可以",
    "没有",
    "进行",
}
LOW_INFORMATION_CJK_CHARACTERS = set("的一是在了和有人我你他也就都而及与着或把被吧呢啊吗哦呀嘛")
BM25_K1 = 1.5
BM25_B = 0.75
HYBRID_RANK_WEIGHT = 0.05
HYBRID_RRF_K = 60
RECENT_REPEAT_MAX_PENALTY = 0.06
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "檔": "档",
        "貨": "货",
        "買": "买",
        "載": "载",
        "這": "这",
        "個": "个",
        "們": "们",
        "說": "说",
        "國": "国",
        "臺": "台",
        "灣": "湾",
        "為": "为",
        "與": "与",
        "對": "对",
        "會": "会",
        "時": "时",
        "後": "后",
        "從": "从",
        "讓": "让",
        "問": "问",
        "題": "题",
        "現": "现",
        "論": "论",
        "應": "应",
        "該": "该",
        "麼": "么",
        "點": "点",
        "發": "发",
        "機": "机",
        "裡": "里",
        "還": "还",
        "沒": "没",
        "開": "开",
        "關": "关",
        "過": "过",
        "進": "进",
        "來": "来",
        "別": "别",
        "見": "见",
        "聽": "听",
        "寫": "写",
        "讀": "读",
        "學": "学",
        "網": "网",
        "線": "线",
        "號": "号",
        "帳": "账",
        "碼": "码",
        "軟": "软",
        "體": "体",
        "實": "实",
        "樣": "样",
        "東": "东",
        "馬": "马",
        "門": "门",
        "車": "车",
        "話": "话",
        "講": "讲",
        "飲": "饮",
        "諗": "谂",
        "邊": "边",
        "幾": "几",
        "尋": "寻",
        "氣": "气",
        "動": "动",
        "數": "数",
        "據": "据",
        "庫": "库",
        "給": "给",
        "請": "请",
        "謝": "谢",
        "幫": "帮",
        "辦": "办",
        "記": "记",
        "認": "认",
        "嗎": "吗",
        "誰": "谁",
        "選": "选",
        "擇": "择",
        "權": "权",
        "驗": "验",
        "證": "证",
    }
)
CJK_SEARCH_ALIASES = (
    ("係唔係", "是不是"),
    ("知唔知", "知不知道"),
    ("明唔明", "明不明白"),
    ("可唔可以", "可不可以"),
    ("得唔得", "可不可以"),
    ("使唔使", "要不要"),
    ("要唔要", "要不要"),
    ("有冇", "有没有"),
    ("唔知道", "不知道"),
    ("唔知", "不知道"),
    ("唔係", "不是"),
    ("唔使", "不用"),
    ("唔好", "不要"),
    ("唔到", "不了"),
    ("唔该", "请"),
    ("点解", "为什么"),
    ("边个", "谁"),
    ("边度", "哪里"),
    ("几时", "什么时候"),
    ("做乜嘢", "做什么"),
    ("做乜", "做什么"),
    ("乜嘢", "什么"),
    ("咩事", "什么事"),
    ("话我知", "告诉我"),
    ("喺度", "在"),
    ("你哋", "你们"),
    ("我哋", "我们"),
    ("佢哋", "他们"),
    ("嗰个", "那个"),
    ("呢个", "这个"),
    ("咁样", "这样"),
    ("而家", "现在"),
    ("听日", "明天"),
    ("寻日", "昨天"),
    ("今日", "今天"),
    ("屋企", "家"),
    ("返工", "上班"),
    ("收工", "下班"),
    ("咩", "什么"),
    ("冇", "没有"),
    ("喺", "在"),
    ("佢", "他"),
    ("咁", "这样"),
    ("咗", "了"),
    ("嘅", "的"),
    ("啲", "些"),
    ("俾", "给"),
    ("畀", "给"),
    ("讲", "说"),
    ("睇", "看"),
    ("食", "吃"),
    ("饮", "喝"),
    ("搵", "找"),
    ("揾", "找"),
    ("谂", "想"),
    ("嚟", "来"),
    ("返", "回"),
    ("干什么", "做什么"),
    ("唔", "不"),
)
LATIN_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "download": ("下载",),
    "downloads": ("下载",),
    "downloading": ("下载",),
}
INTENT_MARKERS = {
    "greeting": ("你好", "您好", "早上好", "晚上好", "晚安", "hello", "hi"),
    "hostile": (
        "傻逼",
        "傻叉",
        "蠢",
        "笨蛋",
        "滚",
        "垃圾",
        "废物",
        "混蛋",
        "妈的",
        "屎",
        "仆街",
        "戇",
        "柒",
        "马来支",
        "支那",
    ),
    "question": ("?", "什么", "怎么", "怎样", "为何", "为什么", "谁", "哪里", "吗", "有没有"),
    "request": (
        "请",
        "帮",
        "麻烦",
        "能不能",
        "可不可以",
        "给我",
        "告诉",
        "应该",
        "需要",
        "必须",
        "先",
        "别",
    ),
    "agreement": ("同意", "赞成", "是的", "没错", "对的", "可以", "好啊", "好的"),
    "disagreement": ("不同意", "反对", "不对", "不是", "不行", "不要", "不可以", "错了"),
}
INTENT_RESPONSE_PREFERENCES = {
    "greeting": ("greeting", "statement"),
    "hostile": ("hostile", "disagreement", "statement"),
    "question": ("statement", "agreement", "disagreement"),
    "request": ("request", "statement"),
    "agreement": ("agreement", "statement"),
    "disagreement": ("disagreement", "hostile", "statement"),
    "statement": ("statement",),
}
SYSTEM_RANDOM = SystemRandom()


class UtteranceSelector(Protocol):
    async def select(
        self,
        *,
        message: str,
        persona_version_id: UUID,
        context: SelectionContext,
        limit: int,
        recent_utterance_ids: Sequence[UUID] | None = None,
        minimum_score: float = 0.0,
        allow_intent_fallback: bool = False,
    ) -> SelectionResult: ...


def informative_cjk_phrase(text: str) -> bool:
    return text not in LOW_INFORMATION_CJK_TOKENS and not all(
        character in LOW_INFORMATION_CJK_CHARACTERS for character in text
    )


def normalized_search_text(text: str) -> str:
    normalized = normalize("NFKC", text).casefold().translate(TRADITIONAL_TO_SIMPLIFIED)
    for source, target in CJK_SEARCH_ALIASES:
        normalized = normalized.replace(source, target)
    return normalized


def normalized_token_list(text: str) -> list[str]:
    lowered = normalized_search_text(text)
    tokens: list[str] = []
    for raw_token in LATIN_TOKEN_PATTERN.findall(lowered):
        variants = dict.fromkeys([raw_token, *ALPHANUMERIC_COMPONENT_PATTERN.findall(raw_token)])
        for token in variants:
            tokens.append(token)
            tokens.extend(LATIN_TOKEN_ALIASES.get(token, ()))
    for sequence in CJK_SEQUENCE_PATTERN.findall(lowered):
        if len(sequence) == 1:
            if sequence not in LOW_INFORMATION_CJK_CHARACTERS:
                tokens.append(sequence)
            continue
        for size in range(2, min(4, len(sequence)) + 1):
            for start in range(len(sequence) - size + 1):
                token = sequence[start : start + size]
                if informative_cjk_phrase(token):
                    tokens.append(token)
    return tokens


def normalized_tokens(text: str) -> set[str]:
    return set(normalized_token_list(text))


def bm25_scores(
    query_tokens: Sequence[str],
    documents: Sequence[Sequence[str]],
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> list[float]:
    if not documents:
        return []
    if not query_tokens:
        return [0.0] * len(documents)
    document_count = len(documents)
    average_length = sum(len(document) for document in documents) / document_count or 1.0
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(document))
    idf = {
        token: log(
            1
            + (document_count - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5)
        )
        for token in set(query_tokens)
        if document_frequency[token]
    }
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        length_ratio = len(document) / average_length
        score = 0.0
        for token, token_idf in idf.items():
            frequency = frequencies[token]
            if not frequency:
                continue
            denominator = frequency + k1 * (1 - b + b * length_ratio)
            score += token_idf * frequency * (k1 + 1) / denominator
        scores.append(score)
    return scores


def positive_rank_positions(scores: Sequence[float]) -> dict[int, int]:
    positions: dict[int, int] = {}
    previous_score: float | None = None
    previous_rank = 0
    for position, (index, score) in enumerate(
        sorted(enumerate(scores), key=lambda item: (-item[1], item[0])), start=1
    ):
        if score <= 0:
            continue
        if previous_score is None or score != previous_score:
            previous_rank = position
            previous_score = score
        positions[index] = previous_rank
    return positions


def normalized_cjk_text(text: str) -> str:
    return "".join(CJK_SEQUENCE_PATTERN.findall(normalized_search_text(text)))


def classify_intent(text: str) -> str:
    normalized = normalized_search_text(text)
    for intent in ("hostile", "greeting", "question", "disagreement", "request", "agreement"):
        if any(marker in normalized for marker in INTENT_MARKERS[intent]):
            return intent
    return "statement"


def intent_fallback_selection(
    *,
    message: str,
    utterances: Sequence[Utterance],
    recent_positions: dict[UUID, int],
    limit: int,
) -> SelectionResult:
    if not utterances:
        return SelectionResult(items=[], coverage_score=0.0)
    requested_intent = classify_intent(message)
    classified = [(utterance, classify_intent(utterance.text)) for utterance in utterances]
    chosen_intent = "any"
    pool = []
    for preferred_intent in INTENT_RESPONSE_PREFERENCES[requested_intent]:
        matching = [
            utterance
            for utterance, utterance_intent in classified
            if utterance_intent == preferred_intent
        ]
        if matching:
            pool = matching
            chosen_intent = preferred_intent
            break
    if not pool:
        pool = list(utterances)
    unseen = [utterance for utterance in pool if utterance.id not in recent_positions]
    if unseen:
        pool = unseen
    SYSTEM_RANDOM.shuffle(pool)
    fallback_score = 0.0
    selected = [
        SelectedUtterance(
            utterance_id=utterance.id,
            text=utterance.text,
            context=utterance.context or "",
            score=fallback_score,
            reason_codes=[
                "intent_random_fallback",
                f"requested_intent_{requested_intent}",
                f"corpus_intent_{chosen_intent}",
            ],
        )
        for utterance in pool[:limit]
    ]
    return SelectionResult(items=selected, coverage_score=fallback_score if selected else 0.0)


def longest_common_cjk_phrase(query: str, candidate: str, *, max_length: int = 32) -> int:
    maximum = min(len(query), len(candidate), max_length)
    for size in range(maximum, 1, -1):
        for start in range(len(query) - size + 1):
            phrase = query[start : start + size]
            if informative_cjk_phrase(phrase) and phrase in candidate:
                return size
    return 0


def stable_tie_key(message: str, context: SelectionContext, utterance_id: UUID) -> str:
    value = f"{context.discord_user_id or ''}|{context.guild_id or ''}|{message}|{utterance_id}"
    return sha256(value.encode("utf-8")).hexdigest()


class LexicalUtteranceSelector:
    def __init__(self, repository: UtteranceRepository) -> None:
        self.repository = repository

    async def select(
        self,
        *,
        message: str,
        persona_version_id: UUID,
        context: SelectionContext,
        limit: int = 5,
        recent_utterance_ids: Sequence[UUID] | None = None,
        minimum_score: float = 0.0,
        allow_intent_fallback: bool = False,
    ) -> SelectionResult:
        message_token_list = normalized_token_list(message)
        message_tokens = set(message_token_list)
        message_cjk = normalized_cjk_text(message)
        recent_positions: dict[UUID, int] = {}
        for position, utterance_id in enumerate(recent_utterance_ids or []):
            recent_positions.setdefault(utterance_id, position)
        now = datetime.now(UTC)
        candidates: list[tuple[SelectedUtterance, float, float, float]] = []
        utterances = await self.repository.list_for_version(persona_version_id, verified=True)
        document_tokens = [
            normalized_token_list(utterance.text + " " + (utterance.topic or ""))
            for utterance in utterances
        ]
        raw_bm25_scores = bm25_scores(message_token_list, document_tokens)
        for utterance, utterance_token_list, raw_bm25_score in zip(
            utterances, document_tokens, raw_bm25_scores, strict=True
        ):
            utterance_tokens = set(utterance_token_list)
            overlap = len(message_tokens & utterance_tokens) / max(1, len(message_tokens))
            score = overlap * 0.8
            reasons = ["lexical_overlap"] if overlap else []
            utterance_cjk = normalized_cjk_text(utterance.text)
            phrase_length = longest_common_cjk_phrase(message_cjk, utterance_cjk)
            if phrase_length:
                phrase_coverage = phrase_length / min(len(message_cjk), 32)
                score = max(score, phrase_coverage * 0.7)
                reasons.append("cjk_phrase_coverage")
            if 2 <= len(message_cjk) <= 32 and message_cjk in utterance_cjk:
                score += 0.15
                reasons.append("exact_cjk_phrase")
            anchored = overlap > 0 or phrase_length > 0
            keyword_score = score
            if context.topic and utterance.topic == context.topic:
                score += 0.12
                reasons.append("topic_match")
                anchored = True
            if context.scene and utterance.scene == context.scene:
                score += 0.08
                reasons.append("scene_match")
                anchored = True
            if anchored and context.relationship and utterance.relationship == context.relationship:
                score += 0.04
                reasons.append("relationship_match")
            if (
                anchored
                and context.audience_type
                and utterance.audience_type == context.audience_type
            ):
                score += 0.02
                reasons.append("audience_match")
            if anchored and utterance.occurred_at:
                occurred = utterance.occurred_at
                if occurred.tzinfo is None:
                    occurred = occurred.replace(tzinfo=UTC)
                age_days = max(0, (now - occurred).days)
                score += max(0.0, 0.02 - min(age_days, 3650) / 182500)
                reasons.append("recency_tiebreaker")
            if raw_bm25_score > 0:
                reasons.append("bm25_keyword_rank")
            if anchored and score > 0:
                candidates.append(
                    (
                        SelectedUtterance(
                            utterance_id=utterance.id,
                            text=utterance.text,
                            context=utterance.context or "",
                            score=round(min(score, 1.0), 6),
                            reason_codes=reasons,
                        ),
                        score,
                        raw_bm25_score,
                        keyword_score,
                    )
                )
        has_supported_keyword = any(
            keyword_score > 0 and item.score >= minimum_score
            for item, _, _, keyword_score in candidates
        )
        if allow_intent_fallback and not has_supported_keyword:
            return intent_fallback_selection(
                message=message,
                utterances=utterances,
                recent_positions=recent_positions,
                limit=limit,
            )
        lexical_ranks = positive_rank_positions([score for _, score, _, _ in candidates])
        bm25_ranks = positive_rank_positions([score for _, _, score, _ in candidates])
        maximum_rrf = 2 / (HYBRID_RRF_K + 1)
        scored: list[tuple[SelectedUtterance, float, float]] = []
        for index, (item, lexical_score, _, keyword_score) in enumerate(candidates):
            ranking_score = lexical_score
            if index in lexical_ranks and index in bm25_ranks:
                reciprocal_rank = 1 / (HYBRID_RRF_K + lexical_ranks[index]) + 1 / (
                    HYBRID_RRF_K + bm25_ranks[index]
                )
                ranking_score += HYBRID_RANK_WEIGHT * reciprocal_rank / maximum_rrf
                item.reason_codes.append("hybrid_retrieval_fusion")
            if item.utterance_id in recent_positions:
                repeat_penalty = RECENT_REPEAT_MAX_PENALTY / (
                    recent_positions[item.utterance_id] + 1
                )
                ranking_score = max(0.0, ranking_score - repeat_penalty)
                item.reason_codes.append("recent_repeat_penalty")
            scored.append((item, keyword_score, ranking_score))
        scored.sort(
            key=lambda entry: (
                entry[0].score < minimum_score,
                -entry[1],
                -entry[2],
                stable_tie_key(message, context, entry[0].utterance_id),
                str(entry[0].utterance_id),
            )
        )
        selected = [item for item, _, _ in scored[:limit]]
        coverage = selected[0].score if selected else 0.0
        return SelectionResult(items=selected, coverage_score=round(coverage, 6))
