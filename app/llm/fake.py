from collections.abc import Iterable

from app.schemas.chat import PersonaGenerationRequest


class FakeLLMClient:
    def __init__(self, responses: Iterable[str] | None = None, *, record_calls=True) -> None:
        self.responses = list(responses or [])
        self.calls: list[PersonaGenerationRequest] = []
        self.record_calls = record_calls

    async def generate(self, request: PersonaGenerationRequest) -> str:
        if self.record_calls:
            self.calls.append(request)
        if self.responses:
            return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return f"{request.resolved_address}，唔知喎，睇下先啦。"
