from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.openai_compatible import OpenAICompatibleLLMClient


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "llm_provider": "openai_compatible",
        "llm_api_key": "local-test-key",
        "llm_model": "test-model",
    }
    values.update(overrides)
    return Settings(**values)


def _request():
    return SimpleNamespace(
        system_prompt="system",
        corrective_instruction=None,
        history=[SimpleNamespace(role="assistant", content="prior")],
        user_message="current",
    )


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "prior"},
        {"role": "user", "content": "current"},
    ]


def test_legacy_request_body_is_exact_when_optional_settings_are_unset():
    body = OpenAICompatibleLLMClient(_settings())._request_body(_messages())
    assert body == {
        "model": "test-model",
        "messages": _messages(),
        "temperature": 0.3,
    }


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"llm_reasoning_effort": "none"}, {"reasoning_effort": "none"}),
        ({"llm_max_tokens": 256}, {"max_tokens": 256}),
        (
            {"llm_reasoning_effort": "none", "llm_max_tokens": 256},
            {"reasoning_effort": "none", "max_tokens": 256},
        ),
        (
            {"llm_temperature": 0.6, "llm_top_p": 0.9},
            {"temperature": 0.6, "top_p": 0.9},
        ),
    ],
)
def test_optional_llm_fields_are_configuration_driven(overrides, expected):
    body = OpenAICompatibleLLMClient(_settings(**overrides))._request_body(_messages())
    assert {key: body[key] for key in expected} == expected


def test_blank_optional_llm_values_are_omitted():
    settings = _settings(llm_reasoning_effort="  ", llm_max_tokens="", llm_top_p="")
    assert settings.llm_reasoning_effort is None
    assert settings.llm_max_tokens is None
    assert settings.llm_top_p is None
    assert OpenAICompatibleLLMClient(settings)._request_body(_messages()) == {
        "model": "test-model",
        "messages": _messages(),
        "temperature": 0.3,
    }


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_llm_max_tokens_are_rejected(value):
    with pytest.raises(ValidationError, match="LLM max tokens must be positive"):
        _settings(llm_max_tokens=value)


@pytest.mark.parametrize("value", [-0.1, 2.1])
def test_out_of_range_llm_temperature_is_rejected(value):
    with pytest.raises(ValidationError, match="temperature must be between"):
        _settings(llm_temperature=value)


@pytest.mark.parametrize("value", [0, -0.1, 1.1])
def test_out_of_range_llm_top_p_is_rejected(value):
    with pytest.raises(ValidationError, match="top-p must be greater"):
        _settings(llm_top_p=value)


def test_non_positive_llm_timeout_is_rejected():
    with pytest.raises(ValidationError, match="LLM timeout must be positive"):
        _settings(llm_timeout_seconds=0)


def test_base_url_never_changes_request_schema():
    local = _settings(llm_base_url="http://127.0.0.1:11434/v1")
    remote = _settings(llm_base_url="https://provider.example/v1")
    assert OpenAICompatibleLLMClient(local)._request_body(_messages()) == (
        OpenAICompatibleLLMClient(remote)._request_body(_messages())
    )


@pytest.mark.asyncio
async def test_generate_sends_configured_body_and_extracts_content(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "收到"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    client = OpenAICompatibleLLMClient(
        _settings(
            llm_reasoning_effort="none",
            llm_max_tokens=256,
            llm_temperature=0.6,
            llm_top_p=0.9,
            llm_timeout_seconds=120,
        )
    )
    assert await client.generate(_request()) == "收到"
    assert captured["json"] == {
        "model": "test-model",
        "messages": _messages(),
        "temperature": 0.6,
        "reasoning_effort": "none",
        "max_tokens": 256,
        "top_p": 0.9,
    }
    assert captured["client_kwargs"]["timeout"] == 120


@pytest.mark.asyncio
async def test_generate_maps_timeout_and_invalid_response(monkeypatch):
    class TimeoutClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def post(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    with pytest.raises(AppError) as timeout:
        await OpenAICompatibleLLMClient(_settings()).generate(_request())
    assert timeout.value.code == "llm_timeout"

    class InvalidClient(TimeoutClient):
        async def post(self, url, **_kwargs):
            return httpx.Response(
                200,
                json={"choices": []},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", InvalidClient)
    with pytest.raises(AppError) as invalid:
        await OpenAICompatibleLLMClient(_settings()).generate(_request())
    assert invalid.value.code == "llm_invalid_response"
