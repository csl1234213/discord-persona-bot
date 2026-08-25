from collections.abc import Mapping
from typing import Any

from app.schemas.chat import ResolvedPersonality

DEFAULTS = {
    "directness": 0.5,
    "warmth": 0.5,
    "humor": 0.2,
    "sarcasm": 0.1,
    "patience": 0.5,
    "empathy": 0.5,
    "assertiveness": 0.5,
    "skepticism": 0.3,
    "formality": 0.5,
    "verbosity": 0.5,
    "emotional_intensity": 0.3,
}
TRAITS = tuple(DEFAULTS)
SYSTEM_MAXIMUMS = {"sarcasm": 0.6, "emotional_intensity": 0.8}


class PersonalityResolver:
    def _values(self, source: Any | None) -> dict[str, Any]:
        if source is None:
            return {}
        if isinstance(source, Mapping):
            return dict(source)
        if hasattr(source, "model_dump"):
            return source.model_dump(exclude_none=True)
        return {key: getattr(source, key) for key in (*TRAITS, "conflict_style")}

    def resolve(
        self,
        published_defaults: Any | None,
        guild_defaults: Any | None = None,
        session_overrides: Any | None = None,
    ) -> ResolvedPersonality:
        values: dict[str, Any] = {**DEFAULTS, "conflict_style": "calm"}
        sources = {key: "published_default" for key in values}
        for source, label in (
            (published_defaults, "published_default"),
            (guild_defaults, "guild"),
            (session_overrides, "session"),
        ):
            for key, value in self._values(source).items():
                if key not in values or value is None:
                    continue
                if key in TRAITS and not 0 <= float(value) <= 1:
                    raise ValueError(f"{key} must be between 0 and 1")
                values[key] = value
                sources[key] = label
        for key, maximum in SYSTEM_MAXIMUMS.items():
            if values[key] > maximum:
                values[key] = maximum
                sources[key] = "system_limit"
        return ResolvedPersonality(**values, sources=sources)
