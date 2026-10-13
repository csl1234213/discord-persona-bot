from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from app.core.config import Settings


class AbrasiveIntensity(StrEnum):
    NORMAL = "NORMAL"
    TEASING = "TEASING"
    ABRASIVE = "ABRASIVE"


@dataclass(frozen=True)
class AbrasiveStyleDecision:
    intensity: AbrasiveIntensity
    abstract_descriptor: str
    trigger_detected: bool
    seed_bucket: int


class AbrasiveStylePolicy:
    VERSION = "abrasive-style-v1"
    TRIGGERS = (
        "吐槽",
        "烦",
        "煩",
        "嬲",
        "生气",
        "无聊",
        "傻",
        "蠢",
        "垃圾",
        "玩笑",
        "讲笑",
        "收皮",
        "仆街",
    )
    DESCRIPTORS = {
        AbrasiveIntensity.NORMAL: (
            "Use a short, direct, natural Cantonese reply. Stay calm and do not add insults."
        ),
        AbrasiveIntensity.TEASING: (
            "Answer first. Optionally add one brief playful or mildly impatient aside; no slurs."
        ),
        AbrasiveIntensity.ABRASIVE: (
            "Answer first. At most one brief gruff aside; "
            "no threats, harassment, slurs, or politics."
        ),
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def choose(
        self,
        *,
        conversation_id: str,
        turn_key: str,
        user_message: str,
        forced_intensity: AbrasiveIntensity | None = None,
    ) -> AbrasiveStyleDecision:
        digest = sha256(
            f"{self.VERSION}|{conversation_id}|{turn_key}".encode()
        ).digest()
        total = (
            self.settings.persona_abrasive_normal_weight
            + self.settings.persona_abrasive_teasing_weight
            + self.settings.persona_abrasive_abrasive_weight
        )
        bucket = int.from_bytes(digest[:8], "big") % total
        trigger = any(marker in user_message.casefold() for marker in self.TRIGGERS)
        if not self.settings.persona_abrasive_style_enabled:
            intensity = AbrasiveIntensity.NORMAL
        elif forced_intensity is not None:
            intensity = forced_intensity
        elif bucket < self.settings.persona_abrasive_normal_weight:
            intensity = AbrasiveIntensity.NORMAL
        elif bucket < (
            self.settings.persona_abrasive_normal_weight
            + self.settings.persona_abrasive_teasing_weight
        ):
            intensity = AbrasiveIntensity.TEASING
        else:
            intensity = AbrasiveIntensity.ABRASIVE
        if intensity == AbrasiveIntensity.ABRASIVE and not trigger:
            intensity = AbrasiveIntensity.TEASING
        return AbrasiveStyleDecision(
            intensity=intensity,
            abstract_descriptor=self.DESCRIPTORS[intensity],
            trigger_detected=trigger,
            seed_bucket=bucket,
        )
