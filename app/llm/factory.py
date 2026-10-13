from app.core.config import Settings
from app.llm.base import LLMClient
from app.llm.failover import FailoverLLMClient
from app.llm.fake import FakeLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLMClient(record_calls=not settings.release_security_mode)

    primary = OpenAICompatibleLLMClient(settings)
    if not settings.llm_fallback_enabled:
        return primary

    fallback_chain_timeout_seconds = settings.llm_fallback_timeout_seconds
    fallback_settings = settings.model_copy(
        update={
            "llm_base_url": settings.llm_fallback_base_url,
            "llm_api_key": settings.llm_fallback_api_key,
            "llm_model": settings.llm_fallback_model or settings.llm_model,
            "llm_timeout_seconds": settings.llm_fallback_timeout_seconds,
            "llm_fallback_enabled": False,
        }
    )
    fallback = OpenAICompatibleLLMClient(fallback_settings)
    if settings.llm_fallback2_enabled:
        tertiary_settings = settings.model_copy(
            update={
                "llm_base_url": settings.llm_fallback2_base_url,
                "llm_api_key": settings.llm_fallback2_api_key,
                "llm_model": settings.llm_fallback2_model or settings.llm_model,
                "llm_timeout_seconds": settings.llm_fallback2_timeout_seconds,
                "llm_max_tokens": min(settings.llm_max_tokens or 96, 96),
                "llm_fallback_enabled": False,
                "llm_fallback2_enabled": False,
            }
        )
        tertiary = OpenAICompatibleLLMClient(tertiary_settings)
        fallback = FailoverLLMClient(
            fallback,
            tertiary,
            primary_timeout_seconds=settings.llm_fallback_timeout_seconds,
            fallback_timeout_seconds=settings.llm_fallback2_timeout_seconds,
            cooldown_seconds=settings.llm_failover_cooldown_seconds,
        )
        # The outer client wraps this nested fallback chain in one deadline.
        # Give it enough time for both the Windows 9B attempt and router 2B.
        fallback_chain_timeout_seconds += settings.llm_fallback2_timeout_seconds
    return FailoverLLMClient(
        primary,
        fallback,
        primary_timeout_seconds=settings.llm_timeout_seconds,
        fallback_timeout_seconds=fallback_chain_timeout_seconds,
        cooldown_seconds=settings.llm_failover_cooldown_seconds,
    )
