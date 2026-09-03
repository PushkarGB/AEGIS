"""Tests for the provider-neutral ModelProvider generation boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.config import ModelConfig, ModelProviderConfig, RuntimeSettings
from aegis.router import (
    APIModelProvider,
    LocalModelProvider,
    MockModelProvider,
    ModelGenerationRequest,
    ModelGenerationResult,
    ModelProvider,
    ModelProviderConfigurationError,
    ModelProviderConnectionError,
    ModelProviderResponseError,
    TemporaryAPIProviderDisabledError,
)


def _make_provider(
    pid: str,
    kind: str,
    endpoint: str,
    *,
    enabled: bool = True,
    api_key_env_var: str | None = None,
) -> ModelProviderConfig:
    return ModelProviderConfig(
        id=pid,
        kind=kind,
        enabled=enabled,
        endpoint=endpoint,
        api_key_env_var=api_key_env_var,
    )


def _make_model(
    mid: str,
    provider: str,
    provider_model_id: str,
) -> ModelConfig:
    return ModelConfig(
        id=mid,
        provider=provider,
        provider_model_id=provider_model_id,
        roles=["agent"],
    )


class RecordingTransport:
    """Fake OpenAI-compatible transport used for deterministic provider tests."""

    def __init__(self, response_payload: object) -> None:
        self.response_payload = response_payload
        self.calls: list[object] = []

    def __call__(self, request: object) -> object:
        self.calls.append(request)
        return self.response_payload


class NativeProtocolTestProvider(ModelProvider):
    """A non-HTTP stand-in for a future native SDK or local runtime adapter."""

    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        return ModelGenerationResult(
            model_id=request.model_id,
            text=f"native-runtime:{request.prompt}",
        )


def _generate_for_agent(
    provider: ModelProvider, request: ModelGenerationRequest
) -> ModelGenerationResult:
    """Represents a future consumer that only knows the abstract provider type."""

    return provider.generate(request)


def test_mock_provider_satisfies_generation_interface():
    provider = MockModelProvider(
        responses={
            "agent_model_placeholder": '{"action":"finish","done":true}',
        }
    )
    request = ModelGenerationRequest(
        model_id="agent_model_placeholder",
        system_prompt="Return a structured decision.",
        prompt="Finish the task.",
    )

    result = _generate_for_agent(provider, request)

    assert isinstance(provider, ModelProvider)
    assert provider.requests == [request]
    assert result.model_id == request.model_id
    assert result.text == '{"action":"finish","done":true}'


def test_mock_provider_is_deterministic_without_custom_responses():
    provider = MockModelProvider()
    request = ModelGenerationRequest(
        model_id="agent_model_placeholder",
        system_prompt="Return concise output.",
        prompt="Prepare an approval note.",
    )

    first = provider.generate(request)
    second = provider.generate(request)

    assert first == second
    assert first.text == (
        "mock:agent_model_placeholder | system=Return concise output. "
        "| prompt=Prepare an approval note."
    )


def test_provider_implementations_are_swappable_behind_model_provider():
    request = ModelGenerationRequest(
        model_id="agent-model",
        prompt="Summarize the observation.",
    )
    local_transport = RecordingTransport(
        {"choices": [{"message": {"content": "local result"}}]}
    )
    api_transport = RecordingTransport(
        {"choices": [{"message": {"content": "api result"}}]}
    )
    providers: list[ModelProvider] = [
        MockModelProvider(responses={"agent-model": "mock result"}),
        NativeProtocolTestProvider(),
        LocalModelProvider(
            _make_provider("local", "local", "http://localhost:11434/v1"),
            [_make_model("agent-model", "local", "qwen3:8b")],
            transport=local_transport,
        ),
        APIModelProvider(
            _make_provider("api", "api", "https://example.invalid/v1"),
            [_make_model("agent-model", "api", "gpt-test")],
            RuntimeSettings(
                environment="testing",
                allow_temporary_api_provider=True,
            ),
            transport=api_transport,
        ),
    ]

    results = [_generate_for_agent(provider, request).text for provider in providers]

    assert results == [
        "mock result",
        "native-runtime:Summarize the observation.",
        "local result",
        "api result",
    ]


def test_provider_config_permits_future_non_http_adapter_kinds():
    config = ModelProviderConfig(
        id="direct_runtime",
        kind="direct_local",
    )

    assert config.endpoint is None


def test_openai_compatible_adapter_requires_endpoint():
    with pytest.raises(ModelProviderConfigurationError, match="require a configured endpoint"):
        LocalModelProvider(
            ModelProviderConfig(id="local", kind="local"),
            [_make_model("agent-model", "local", "qwen3:8b")],
        )


def test_local_model_provider_uses_configured_endpoint_and_provider_model():
    transport = RecordingTransport(
        {"choices": [{"message": {"content": "draft response"}}]}
    )
    provider = LocalModelProvider(
        _make_provider("local", "local", "http://localhost:11434/v1"),
        [_make_model("agent-model", "local", "qwen3:8b")],
        transport=transport,
    )
    request = ModelGenerationRequest(
        model_id="agent-model",
        system_prompt="Be concise.",
        prompt="Draft the result.",
    )

    result = provider.generate(request)
    sent_request = transport.calls[0]

    assert result == ModelGenerationResult(
        model_id="agent-model",
        text="draft response",
    )
    assert getattr(sent_request, "url") == "http://localhost:11434/v1/chat/completions"
    assert getattr(sent_request, "payload") == {
        "model": "qwen3:8b",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Draft the result."},
        ],
        "stream": False,
        "temperature": 0,
    }


def test_local_model_provider_wraps_connectivity_error():
    def failing_transport(_: object) -> object:
        raise OSError("connection refused")

    provider = LocalModelProvider(
        _make_provider("local", "local", "http://localhost:11434/v1"),
        [_make_model("agent-model", "local", "qwen3:8b")],
        transport=failing_transport,
    )

    with pytest.raises(ModelProviderConnectionError, match="Could not reach provider endpoint"):
        provider.generate(
            ModelGenerationRequest(
                model_id="agent-model",
                prompt="Test connectivity.",
            )
        )


def test_local_model_provider_rejects_unknown_model_id():
    provider = LocalModelProvider(
        _make_provider("local", "local", "http://localhost:11434/v1"),
        [_make_model("agent-model", "local", "qwen3:8b")],
        transport=RecordingTransport(
            {"choices": [{"message": {"content": "unused"}}]}
        ),
    )

    with pytest.raises(ModelProviderConfigurationError, match="is not configured for provider"):
        provider.generate(
            ModelGenerationRequest(
                model_id="coding-model",
                prompt="Generate code.",
            )
        )


def test_api_model_provider_requires_explicit_dev_testing_enablement():
    provider_config = _make_provider("api", "api", "https://example.invalid/v1")
    model_configs = [_make_model("agent-model", "api", "gpt-test")]

    with pytest.raises(TemporaryAPIProviderDisabledError, match="disabled by runtime configuration"):
        APIModelProvider(
            provider_config,
            model_configs,
            RuntimeSettings(environment="development"),
        )


def test_api_model_provider_rejects_production_runtime():
    provider_config = _make_provider("api", "api", "https://example.invalid/v1")
    model_configs = [_make_model("agent-model", "api", "gpt-test")]

    with pytest.raises(TemporaryAPIProviderDisabledError, match="not allowed in production"):
        APIModelProvider(
            provider_config,
            model_configs,
            RuntimeSettings(
                environment="production",
                allow_temporary_api_provider=False,
            ),
        )


def test_api_model_provider_uses_configured_auth_and_parses_response(monkeypatch):
    monkeypatch.setenv("AEGIS_TEST_API_KEY", "secret-token")
    transport = RecordingTransport(
        {"choices": [{"message": {"content": [{"type": "text", "text": "api draft"}]}}]}
    )
    provider = APIModelProvider(
        _make_provider(
            "api",
            "api",
            "https://example.invalid/v1",
            api_key_env_var="AEGIS_TEST_API_KEY",
        ),
        [_make_model("agent-model", "api", "gpt-test")],
        RuntimeSettings(
            environment="testing",
            allow_temporary_api_provider=True,
        ),
        transport=transport,
    )

    result = provider.generate(
        ModelGenerationRequest(
            model_id="agent-model",
            prompt="Create a synthetic summary.",
        )
    )
    sent_request = transport.calls[0]

    assert result.text == "api draft"
    assert getattr(sent_request, "headers")["Authorization"] == "Bearer secret-token"
    assert getattr(sent_request, "payload")["model"] == "gpt-test"


def test_api_model_provider_rejects_missing_configured_api_key(monkeypatch):
    monkeypatch.delenv("AEGIS_TEST_API_KEY", raising=False)
    provider = APIModelProvider(
        _make_provider(
            "api",
            "api",
            "https://example.invalid/v1",
            api_key_env_var="AEGIS_TEST_API_KEY",
        ),
        [_make_model("agent-model", "api", "gpt-test")],
        RuntimeSettings(
            environment="testing",
            allow_temporary_api_provider=True,
        ),
        transport=RecordingTransport(
            {"choices": [{"message": {"content": "unused"}}]}
        ),
    )

    with pytest.raises(ModelProviderConfigurationError, match="api_key_env_var"):
        provider.generate(
            ModelGenerationRequest(
                model_id="agent-model",
                prompt="Create a synthetic summary.",
            )
        )


def test_api_model_provider_rejects_malformed_response():
    provider = APIModelProvider(
        _make_provider("api", "api", "https://example.invalid/v1"),
        [_make_model("agent-model", "api", "gpt-test")],
        RuntimeSettings(
            environment="testing",
            allow_temporary_api_provider=True,
        ),
        transport=RecordingTransport({"choices": []}),
    )

    with pytest.raises(ModelProviderResponseError, match="at least one choice"):
        provider.generate(
            ModelGenerationRequest(
                model_id="agent-model",
                prompt="Create a synthetic summary.",
            )
        )


def test_generation_contract_is_json_serializable():
    request = ModelGenerationRequest(
        model_id="coding_model_placeholder",
        prompt="Generate a calculation.",
    )
    result = ModelGenerationResult(
        model_id=request.model_id,
        text="print('calculation')",
    )

    assert request.model_dump(mode="json") == {
        "model_id": "coding_model_placeholder",
        "prompt": "Generate a calculation.",
        "system_prompt": None,
    }
    assert ModelGenerationResult.model_validate(result.model_dump(mode="json")) == result


def test_model_provider_cannot_be_instantiated_without_generate():
    with pytest.raises(TypeError):
        ModelProvider()


@pytest.mark.parametrize(
    "payload",
    [
        {"model_id": "invalid model", "prompt": "Generate."},
        {"model_id": "agent_model", "prompt": ""},
        {"model_id": "agent_model", "prompt": "Generate.", "extra": True},
    ],
)
def test_generation_request_rejects_invalid_provider_specific_data(payload):
    with pytest.raises(ValidationError):
        ModelGenerationRequest.model_validate(payload)
