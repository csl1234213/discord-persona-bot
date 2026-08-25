import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.chat import PersonaGenerationRequest


class OpenAICompatibleLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(self, request: PersonaGenerationRequest) -> str:
        if self.settings.llm_api_key is None:
            raise AppError("llm_not_configured", "LLM API key is not configured.")
        system = request.system_prompt
        if request.corrective_instruction:
            system += f"\n\nCORRECTION:\n{request.corrective_instruction}"
        messages = [{"role": "system", "content": system}]
        messages.extend(
            {"role": item.role, "content": item.content} for item in request.history[-12:]
        )
        messages.append({"role": "user", "content": request.user_message})
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.settings.llm_model,
                        "messages": messages,
                        "temperature": 0.3,
                    },
                )
        except httpx.TimeoutException as exc:
            raise AppError("llm_timeout", "The language model timed out.") from exc
        except httpx.HTTPError as exc:
            raise AppError("llm_provider_error", "The language model provider failed.") from exc
        if response.is_error:
            raise AppError("llm_provider_error", "The language model provider failed.")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError(
                "llm_invalid_response", "The language model returned invalid data."
            ) from exc
