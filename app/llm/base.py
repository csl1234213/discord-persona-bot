from typing import Protocol

from app.schemas.chat import PersonaGenerationRequest


class LLMClient(Protocol):
    async def generate(self, request: PersonaGenerationRequest) -> str: ...


class GenerationReviewer(Protocol):
    async def review(self, request: PersonaGenerationRequest, answer: str) -> bool: ...
