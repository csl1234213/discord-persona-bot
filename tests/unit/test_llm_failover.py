import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import build_llm_client
from app.llm.failover import FailoverLLMClient
from app.llm.fake import FakeLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient


def request():
    return SimpleNamespace(
        system_prompt="system",
        corrective_instruction=None,
        history=[],
        user_message="current",
    )


class StubClient:
    def __init__(self, *effects) -> None:
        self.effects = list(effects)
        self.calls = 0

    async def generate(self, _request) -> str:
        self.calls += 1
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        if callable(effect):
            return await effect()
        return effect


def client(primary, fallback, clock=lambda: 0.0, **overrides) -> FailoverLLMClient:
    return FailoverLLMClient(
        primary,
        fallback,
        primary_timeout_seconds=overrides.get("primary_timeout_seconds", 1),
        fallback_timeout_seconds=overrides.get("fallback_timeout_seconds", 1),
        cooldown_seconds=overrides.get("cooldown_seconds", 60),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback():
    primary = StubClient("primary")
    fallback = StubClient("fallback")

    assert await client(primary, fallback).generate(request()) == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.parametrize(
    "code", ["llm_timeout", "llm_provider_error", "llm_invalid_response"]
)
@pytest.mark.asyncio
async def test_eligible_primary_failure_opens_circuit_and_falls_back(code):
    primary = StubClient(AppError(code, "failed"), "unexpected")
    fallback = StubClient("fallback-1", "fallback-2")
    failover = client(primary, fallback)

    assert await failover.generate(request()) == "fallback-1"
    assert await failover.generate(request()) == "fallback-2"
    assert primary.calls == 1
    assert fallback.calls == 2


@pytest.mark.parametrize("error", [AppError("llm_not_configured", "bad"), RuntimeError()])
@pytest.mark.asyncio
async def test_noneligible_failure_never_calls_fallback(error):
    primary = StubClient(error)
    fallback = StubClient("fallback")

    with pytest.raises(type(error)):
        await client(primary, fallback).generate(request())
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_cancellation_propagates_without_fallback():
    primary = StubClient(asyncio.CancelledError())
    fallback = StubClient("fallback")

    with pytest.raises(asyncio.CancelledError):
        await client(primary, fallback).generate(request())
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_hard_primary_deadline_falls_back():
    async def slow() -> str:
        await asyncio.sleep(1)
        return "late"

    primary = StubClient(slow)
    fallback = StubClient("fallback")

    result = await client(
        primary, fallback, primary_timeout_seconds=0.01
    ).generate(request())
    assert result == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_failure_propagates_without_third_attempt():
    primary_error = AppError("llm_timeout", "primary")
    fallback_error = AppError("llm_provider_error", "fallback")
    primary = StubClient(primary_error, "unexpected")
    fallback = StubClient(fallback_error)

    with pytest.raises(AppError) as raised:
        await client(primary, fallback).generate(request())
    assert raised.value is fallback_error
    assert raised.value.__cause__ is primary_error
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_exactly_one_half_open_probe_and_recovery():
    now = [0.0]
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def recovered() -> str:
        probe_started.set()
        await release_probe.wait()
        return "recovered"

    primary = StubClient(AppError("llm_timeout", "failed"), recovered, "primary")
    fallback = StubClient("fallback-1", "fallback-2")
    failover = client(primary, fallback, clock=lambda: now[0])

    assert await failover.generate(request()) == "fallback-1"
    now[0] = 60.0
    probe = asyncio.create_task(failover.generate(request()))
    await probe_started.wait()
    assert await failover.generate(request()) == "fallback-2"
    release_probe.set()
    assert await probe == "recovered"
    assert await failover.generate(request()) == "primary"
    assert primary.calls == 3
    assert fallback.calls == 2


@pytest.mark.asyncio
async def test_failed_half_open_probe_reopens_for_full_cooldown():
    now = [0.0]
    primary = StubClient(
        AppError("llm_timeout", "first"),
        AppError("llm_provider_error", "probe"),
        "recovered",
    )
    fallback = StubClient("fallback-1", "fallback-2", "fallback-3")
    failover = client(primary, fallback, clock=lambda: now[0])

    assert await failover.generate(request()) == "fallback-1"
    now[0] = 60.0
    assert await failover.generate(request()) == "fallback-2"
    now[0] = 119.9
    assert await failover.generate(request()) == "fallback-3"
    assert primary.calls == 2


def failover_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "llm_provider": "openai_compatible",
        "llm_base_url": "http://primary:11434/v1",
        "llm_api_key": "primary-key",
        "llm_model": "test-model",
        "llm_reasoning_effort": "none",
        "llm_max_tokens": 256,
        "llm_temperature": 0.6,
        "llm_top_p": 0.9,
        "llm_timeout_seconds": 30,
        "llm_fallback_enabled": True,
        "llm_fallback_base_url": "http://fallback:11434/v1",
        "llm_fallback_api_key": "fallback-key",
        "llm_fallback_timeout_seconds": 480,
        "llm_failover_cooldown_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def test_factory_preserves_legacy_client_when_fallback_disabled():
    fake = build_llm_client(Settings(_env_file=None, llm_provider="fake"))
    compatible = build_llm_client(
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            llm_api_key="key",
        )
    )

    assert isinstance(fake, FakeLLMClient)
    assert isinstance(compatible, OpenAICompatibleLLMClient)


def test_factory_changes_only_endpoint_key_and_timeout_for_fallback():
    settings = failover_settings()
    built = build_llm_client(settings)

    assert isinstance(built, FailoverLLMClient)
    assert isinstance(built.primary, OpenAICompatibleLLMClient)
    assert isinstance(built.fallback, OpenAICompatibleLLMClient)
    primary = built.primary.settings
    fallback = built.fallback.settings
    assert fallback.llm_base_url == "http://fallback:11434/v1"
    assert fallback.llm_api_key.get_secret_value() == "fallback-key"
    assert fallback.llm_timeout_seconds == 480
    for field in (
        "llm_model",
        "llm_reasoning_effort",
        "llm_max_tokens",
        "llm_temperature",
        "llm_top_p",
    ):
        assert getattr(fallback, field) == getattr(primary, field)


def test_factory_can_use_a_different_fallback_model():
    settings = failover_settings(
        llm_model="qwen3.5:9b-q4_K_M",
        llm_fallback_model="qwen3.5:2b-q4_K_M",
    )
    built = build_llm_client(settings)

    assert isinstance(built, FailoverLLMClient)
    assert built.primary.settings.llm_model == "qwen3.5:9b-q4_K_M"
    assert built.fallback.settings.llm_model == "qwen3.5:2b-q4_K_M"


def test_factory_reserves_time_for_each_fallback_tier():
    settings = failover_settings(
        llm_fallback_timeout_seconds=60,
        llm_fallback2_enabled=True,
        llm_fallback2_base_url="http://router:11434/v1",
        llm_fallback2_api_key="router-key",
        llm_fallback2_model="qwen2b",
        llm_fallback2_timeout_seconds=120,
    )
    built = build_llm_client(settings)

    assert isinstance(built, FailoverLLMClient)
    assert isinstance(built.fallback, FailoverLLMClient)
    assert built.fallback.primary_timeout_seconds == 60
    assert built.fallback.fallback_timeout_seconds == 120
    assert built.fallback_timeout_seconds == 180


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"llm_fallback_base_url": ""}, "fallback base URL"),
        ({"llm_fallback_api_key": ""}, "fallback API key"),
        ({"llm_provider": "fake"}, "openai_compatible"),
        (
            {"llm_fallback_base_url": "HTTP://PRIMARY:11434/v1/"},
            "base URLs must differ",
        ),
        ({"llm_fallback_timeout_seconds": 0}, "timeout must be positive"),
        ({"llm_failover_cooldown_seconds": 0}, "cooldown must be positive"),
    ],
)
def test_invalid_failover_configuration_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        failover_settings(**overrides)
