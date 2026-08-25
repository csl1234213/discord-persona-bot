from collections.abc import Iterable

from app.schemas.chat import PersonaGenerationRequest


class FakeLLMClient:
    def __init__(self, responses: Iterable[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[PersonaGenerationRequest] = []

    async def generate(self, request: PersonaGenerationRequest) -> str:
        self.calls.append(request)
        if self.responses:
            return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        evidence = request.selected_utterances[0].text.strip().rstrip("。！？")[:120]
        return f"{request.resolved_address}，按现有材料来看，{evidence}。"
