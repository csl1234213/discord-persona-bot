import asyncio
import logging
import time
from collections.abc import Callable
from typing import Literal

from app.core.errors import AppError
from app.llm.base import LLMClient
from app.schemas.chat import PersonaGenerationRequest

logger = logging.getLogger(__name__)

FAILOVER_ERROR_CODES = frozenset(
    {"llm_timeout", "llm_provider_error", "llm_invalid_response"}
)
CircuitState = Literal["closed", "open", "half_open"]


class FailoverLLMClient:
    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient,
        *,
        primary_timeout_seconds: float,
        fallback_timeout_seconds: float,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_timeout_seconds = primary_timeout_seconds
        self.fallback_timeout_seconds = fallback_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._state: CircuitState = "closed"
        self._open_until = 0.0
        self._epoch = 0

    async def generate(self, request: PersonaGenerationRequest) -> str:
        route, epoch = await self._choose_route()
        if route == "fallback":
            logger.info("LLM primary skipped circuit_state=%s", self._state)
            return await self._call_fallback(request)

        is_probe = route == "probe"
        if is_probe:
            logger.info("LLM primary half-open probe started")
        try:
            answer = await self._call_with_deadline(
                self.primary,
                request,
                self.primary_timeout_seconds,
            )
        except AppError as exc:
            if exc.code not in FAILOVER_ERROR_CODES:
                raise
            await self._record_primary_failure(epoch, is_probe)
            logger.warning("LLM primary failed code=%s fallback=true", exc.code)
            return await self._call_fallback(request, primary_error=exc)

        if is_probe:
            await self._record_probe_success(epoch)
        return answer

    async def _choose_route(self) -> tuple[Literal["primary", "fallback", "probe"], int]:
        async with self._lock:
            now = self._clock()
            if self._state == "open":
                if now < self._open_until:
                    return "fallback", self._epoch
                self._state = "half_open"
                self._epoch += 1
                return "probe", self._epoch
            if self._state == "half_open":
                return "fallback", self._epoch
            return "primary", self._epoch

    async def _record_primary_failure(self, epoch: int, is_probe: bool) -> None:
        async with self._lock:
            expected_state = "half_open" if is_probe else "closed"
            if self._state != expected_state or self._epoch != epoch:
                return
            self._state = "open"
            self._open_until = self._clock() + self.cooldown_seconds
            self._epoch += 1
            logger.warning("LLM primary circuit opened cooldown_seconds=%s", self.cooldown_seconds)

    async def _record_probe_success(self, epoch: int) -> None:
        async with self._lock:
            if self._state != "half_open" or self._epoch != epoch:
                return
            self._state = "closed"
            self._open_until = 0.0
            self._epoch += 1
            logger.info("LLM primary circuit recovered")

    async def _call_fallback(
        self,
        request: PersonaGenerationRequest,
        *,
        primary_error: AppError | None = None,
    ) -> str:
        try:
            return await self._call_with_deadline(
                self.fallback,
                request,
                self.fallback_timeout_seconds,
            )
        except AppError as exc:
            logger.error(
                "LLM fallback failed code=%s primary_code=%s",
                exc.code,
                primary_error.code if primary_error is not None else "none",
            )
            if primary_error is not None:
                raise exc from primary_error
            raise

    @staticmethod
    async def _call_with_deadline(
        client: LLMClient,
        request: PersonaGenerationRequest,
        timeout_seconds: float,
    ) -> str:
        try:
            async with asyncio.timeout(timeout_seconds):
                return await client.generate(request)
        except TimeoutError as exc:
            raise AppError("llm_timeout", "The language model timed out.") from exc
