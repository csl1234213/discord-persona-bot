import re
from dataclasses import dataclass

from app.schemas.chat import HistoryMessage
from app.services.utterance_selector import normalized_search_text


@dataclass(frozen=True)
class PoliticalDecision:
    is_political: bool
    reason_codes: tuple[str, ...]
    concept_codes: tuple[str, ...] = ()
    relation_codes: tuple[str, ...] = ()
    context_codes: tuple[str, ...] = ()


class PoliticalTopicRouter:
    """Deterministic political-topic classification with explainable decisions."""

    CONCEPTS = {
        "party": (
            "政党",
            "共产党",
            "国民党",
            "民进党",
            "民主党",
            "共和党",
        ),
        "politician": ("政治人物", "总统", "主席", "特首", "议员", "首相", "领导人"),
        "policy": ("政治政策", "政府政策", "政策", "法案", "国安法", "国家安全法", "政治制裁"),
        "sovereignty": ("主权", "领土归属"),
        "independence": ("港独", "台独", "政治独立", "国家独立"),
        "unification": ("国家统一", "两岸统一", "统一台湾"),
        "national_security": ("国安政治", "国安法", "国家安全法"),
        "protest": ("示威", "抗争", "政治游行"),
        "movement": ("政治运动", "民主运动", "白纸运动"),
        "system": ("政治制度", "民主制度", "一党制", "多党制"),
        "election": ("选举", "竞选", "政治投票"),
    }
    RELATIONS = {
        "opinion": ("点睇", "点看", "怎么看", "如何看", "评价", "看法", "认为", "觉得"),
        "stance": ("支持", "反对", "赞成", "拥护", "抵制", "立场"),
        "governance": ("执政", "下台", "当选", "参选", "投票", "管治"),
        "sovereignty_relation": ("归属", "属于", "分裂", "统一", "独立"),
        "political_action": ("制裁", "镇压", "颁布", "修法", "废除", "抗议"),
        "issue": ("问题", "争议", "事件", "议题"),
        "hostile_stance": ("滚", "垃圾", "卖国", "汉奸", "叛徒"),
    }
    CONTEXTS = {
        "hong_kong": ("香港", "港府"),
        "taiwan": ("台湾", "台海", "两岸"),
        "china": ("中国", "内地", "大陆", "北京"),
        "united_states": ("美国", "华盛顿"),
        "united_kingdom": ("英国", "伦敦"),
        "government": ("政府", "政权", "国会", "立法会", "议会"),
    }
    NONPOLITICAL_DOMAINS = {
        "weather": ("落雨", "下雨", "天气", "气温", "台风"),
        "food": ("好食", "餐厅", "饭店", "夜市", "饮茶", "咖啡"),
        "travel": ("旅游", "景点", "酒店", "机票", "搭车", "地铁"),
        "sports": ("比赛", "球队", "足球", "篮球", "跑步"),
    }
    COLLISIONS = {
        "unification": ("统一格式", "统一接口", "统一规范", "统一版本", "统一回复"),
        "independence": ("独立完成", "独立运行", "独立账户", "独立房间", "独立项目"),
        "policy": ("缓存策略", "重试策略", "安全策略", "软件政策"),
    }
    REFERENCE_MARKERS = ("呢件事", "这件事", "呢个", "这个", "嗰个", "那个", "佢", "他")

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = normalized_search_text(text)
        translations = {
            "黨": "党",
            "總統": "总统",
            "議員": "议员",
            "領導": "领导",
            "國安": "国安",
            "獨立": "独立",
            "統一": "统一",
            "選舉": "选举",
            "參選": "参选",
            "執政": "执政",
            "製裁": "制裁",
            "鎮壓": "镇压",
            "爭議": "争议",
            "議題": "议题",
            "點睇": "点睇",
            "怎麼看": "怎么看",
            "評價": "评价",
            "認為": "认为",
            "覺得": "觉得",
            "贊成": "赞成",
            "擁護": "拥护",
            "歸屬": "归属",
            "屬於": "属于",
            "廢除": "废除",
            "遊行": "游行",
            "運動": "运动",
            "氣溫": "气温",
            "颱風": "台风",
            "旅遊": "旅游",
            "餐廳": "餐厅",
            "飯店": "饭店",
            "機票": "机票",
            "比賽": "比赛",
            "球隊": "球队",
        }
        for source, target in translations.items():
            normalized = normalized.replace(source, target)
        return re.sub(r"[\u200b-\u200d\ufeff]", "", normalized)

    @staticmethod
    def _matches(text: str, mapping: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
        return tuple(code for code, terms in mapping.items() if any(term in text for term in terms))

    def route(
        self, message: str, history: list[HistoryMessage] | tuple[HistoryMessage, ...] = ()
    ) -> PoliticalDecision:
        text = self._normalize(message)
        collision_codes = self._matches(text, self.COLLISIONS)
        concept_codes = tuple(
            code
            for code in self._matches(text, self.CONCEPTS)
            if code not in collision_codes
        )
        relation_codes = self._matches(text, self.RELATIONS)
        context_codes = self._matches(text, self.CONTEXTS)
        domain_codes = self._matches(text, self.NONPOLITICAL_DOMAINS)

        reasons = [f"NONPOL_COLLISION:{code}" for code in collision_codes]
        if concept_codes and relation_codes:
            reasons.extend(f"POL_CORE:{code}" for code in concept_codes)
            reasons.extend(f"POL_RELATION:{code}" for code in relation_codes)
            reasons.extend(f"POL_CONTEXT:{code}" for code in context_codes)
            reasons.append("POL_SAME_CLAUSE")
            return PoliticalDecision(
                True,
                tuple(reasons),
                concept_codes,
                relation_codes,
                context_codes,
            )

        has_reference = any(marker in text for marker in self.REFERENCE_MARKERS)
        if not concept_codes and relation_codes and has_reference:
            previous_user = next((item for item in reversed(history) if item.role == "user"), None)
            if previous_user is not None:
                prior = self.route(previous_user.content)
                if prior.is_political:
                    return PoliticalDecision(
                        True,
                        (
                            *prior.reason_codes,
                            *(f"POL_RELATION:{code}" for code in relation_codes),
                            "POL_PRIOR_USER_CONTEXT",
                        ),
                        prior.concept_codes,
                        relation_codes,
                        prior.context_codes,
                    )

        if context_codes and not concept_codes:
            reasons.append("NONPOL_GEO_ONLY")
        reasons.extend(f"NONPOL_DOMAIN:{code}" for code in domain_codes)
        return PoliticalDecision(
            False,
            tuple(reasons or ("NONPOL_NO_POLITICAL_TUPLE",)),
            concept_codes,
            relation_codes,
            context_codes,
        )
