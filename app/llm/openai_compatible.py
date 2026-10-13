import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.chat import PersonaGenerationRequest


class OpenAICompatibleLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _post(self, client, url, **kwargs):
        if not self.settings.release_security_mode:
            return await client.post(url, **kwargs)
        content = bytearray()
        async with client.stream("POST", url, **kwargs) as response:
            async for chunk in response.aiter_bytes(chunk_size=8192):
                content.extend(chunk)
                if len(content) > 131072:
                    raise AppError("llm_invalid_response", "Provider body exceeds size limit.")
            return httpx.Response(
                response.status_code, content=bytes(content), request=response.request
            )

    def _request_body(self, messages: list[dict[str, str]]) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if self.settings.llm_reasoning_effort is not None:
            body["reasoning_effort"] = self.settings.llm_reasoning_effort
        if self.settings.llm_max_tokens is not None:
            body["max_tokens"] = self.settings.llm_max_tokens
        if self.settings.llm_top_p is not None:
            body["top_p"] = self.settings.llm_top_p
        return body

    def _ollama_native_body(self, messages: list[dict[str, str]]) -> dict[str, object]:
        options: dict[str, object] = {"temperature": self.settings.llm_temperature}
        body: dict[str, object] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": options,
        }
        if self.settings.llm_max_tokens is not None:
            options["num_predict"] = self.settings.llm_max_tokens
        return body

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
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                if self.settings.llm_ollama_native:
                    native = await self._post(
                        client,
                        f"{self.settings.llm_base_url.rstrip('/').removesuffix('/v1')}/api/chat",
                        headers=headers,
                        json=self._ollama_native_body(messages),
                    )
                    if native.is_error:
                        raise AppError("llm_provider_error", "The language model provider failed.")
                    message = native.json().get("message", {})
                    text = message.get("content")
                    if isinstance(text, str) and text.strip():
                        return text
                    raise AppError(
                        "llm_invalid_response", "The language model returned invalid data."
                    )
                response = await self._post(
                    client,
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=self._request_body(messages),
                )
        except (ValueError, TypeError, AttributeError) as exc:
            raise AppError(
                "llm_invalid_response", "The language model returned invalid data."
            ) from exc
        except httpx.TimeoutException as exc:
            raise AppError("llm_timeout", "The language model timed out.") from exc
        except httpx.HTTPError as exc:
            raise AppError("llm_provider_error", "The language model provider failed.") from exc
        if response.is_error:
            base_url = self.settings.llm_base_url.rstrip("/")
            if "11434" in base_url:
                try:
                    async with httpx.AsyncClient(
                        timeout=self.settings.llm_timeout_seconds
                    ) as retry_client:
                        native = await self._post(
                            retry_client,
                            f"{base_url.removesuffix('/v1')}/api/chat",
                            headers=headers,
                            json=self._ollama_native_body(messages),
                        )
                    if not native.is_error:
                        text = native.json().get("message", {}).get("content")
                        if isinstance(text, str) and text.strip():
                            return text
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    pass
            raise AppError("llm_provider_error", "The language model provider failed.")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AppError(
                "llm_invalid_response", "The language model returned invalid data."
            ) from exc
