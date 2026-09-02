"""Tests for the provider-neutral ModelProvider generation boundary."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from aegis.router import ModelGenerationRequest, ModelGenerationResult, ModelProvider


class StaticMockModelProvider(ModelProvider):
    """In-memory provider used to verify consumers need no concrete adapter."""

    def __init__(self, response_factory: Callable[[ModelGenerationRequest], str]) -> None:
        self.response_factory = response_factory
        self.requests: list[ModelGenerationRequest] = []

    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        self.requests.append(request)
        return ModelGenerationResult(
            model_id=request.model_id,
            text=self.response_factory(request),
        )


def _generate_for_agent(
    provider: ModelProvider, request: ModelGenerationRequest
) -> ModelGenerationResult:
    """Represents a future consumer that only knows the abstract provider type."""

    return provider.generate(request)


def test_mock_provider_satisfies_generation_interface():
    provider = StaticMockModelProvider(
        lambda request: f"Draft for {request.model_id}: {request.prompt}"
    )
    request = ModelGenerationRequest(
        model_id="agent_model_placeholder",
        system_prompt="Return a concise draft.",
        prompt="Prepare an approval note.",
    )

    result = _generate_for_agent(provider, request)

    assert isinstance(provider, ModelProvider)
    assert provider.requests == [request]
    assert result.model_id == request.model_id
    assert result.text == "Draft for agent_model_placeholder: Prepare an approval note."


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
