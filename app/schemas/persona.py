from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PersonaCreate(ORMModel):
    display_name: str = Field(min_length=1, max_length=200)
    description: str = ""
    coverage_threshold: float = Field(default=0.05, ge=0, le=1)


class VersionCreate(ORMModel):
    version: str = Field(min_length=1, max_length=40)
    change_notes: str = ""
    clone_from_version_id: UUID | None = None


class UtteranceCreate(ORMModel):
    text: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=4000)
    topic: str | None = None
    audience_type: str | None = None
    relationship: str | None = None
    scene: str | None = None
    emotion: str | None = None
    source_type: str = "authorized"
    source_reference: str = ""
    verified: bool = False


Trait = Annotated[float, Field(ge=0, le=1)]


class PersonalityValues(ORMModel):
    directness: Trait = 0.5
    warmth: Trait = 0.5
    humor: Trait = 0.2
    sarcasm: Trait = 0.1
    patience: Trait = 0.5
    empathy: Trait = 0.5
    assertiveness: Trait = 0.5
    skepticism: Trait = 0.3
    formality: Trait = 0.5
    verbosity: Trait = 0.5
    emotional_intensity: Trait = 0.3
    conflict_style: str = "calm"


class ImportPreview(ORMModel):
    filename: str
    items: list[UtteranceCreate]
    duplicate_count: int = 0
    line_count: int


class LanguageAnalysis(ORMModel):
    frequent_words: list[dict]
    frequent_phrases: list[dict]
    candidate_catchphrases: list[dict]
    sentence_length: dict
    punctuation_frequencies: dict[str, int]
    common_openings: list[dict]
    common_transitions: list[dict]
    addresses: list[dict]
    published: bool = False
