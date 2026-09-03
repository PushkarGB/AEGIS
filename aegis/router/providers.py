"""Concrete ModelProvider adapters for mock, local, and temporary API usage."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request

from aegis.config import ModelConfig, ModelProviderConfig, RuntimeSettings

from .provider import ModelGenerationRequest, ModelGenerationResult, ModelProvider


class ModelProviderError(RuntimeError):
    """Base error raised by concrete model-provider adapters."""


class ModelProviderConfigurationError(ModelProviderError, ValueError):
    """Raised when provider configuration is invalid for the requested operation."""


class ModelProviderConnectionError(ModelProviderError):
    """Raised when a provider endpoint cannot be reached successfully."""


class ModelProviderResponseError(ModelProviderError):
    """Raised when a provider returns malformed or incomplete data."""


class TemporaryAPIProviderDisabledError(ModelProviderConfigurationError):
    """Raised when the temporary API provider is used outside allowed runtime policy."""


@dataclass(frozen=True)
class _TransportRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, object]
    timeout_seconds: int


Transport = Callable[[_TransportRequest], object]


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _default_mock_response(request: ModelGenerationRequest) -> str:
    parts = [f"mock:{request.model_id}"]
    if request.system_prompt is not None:
        parts.append(f"system={_normalize_text(request.system_prompt)}")
    parts.append(f"prompt={_normalize_text(request.prompt)}")
    return " | ".join(parts)


def _build_provider_model_map(
    provider_id: str,
    model_configs: Iterable[ModelConfig],
) -> dict[str, str]:
    return {
        model.id: model.provider_model_id or model.id
        for model in model_configs
        if model.provider == provider_id
    }


def _chat_completions_url(endpoint: str) -> str:
    trimmed = endpoint.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def _coerce_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        joined = "".join(parts)
        if joined:
            return joined
    raise ModelProviderResponseError(
        "OpenAI-compatible response must contain assistant message text."
    )


def _default_transport(request: _TransportRequest) -> object:
    body = json.dumps(request.payload).encode("utf-8")
    http_request = urllib_request.Request(
        request.url,
        data=body,
        headers=request.headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(
            http_request,
            timeout=request.timeout_seconds,
        ) as response:
            payload = response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise ModelProviderConnectionError(
                f"Provider endpoint '{request.url}' returned HTTP {exc.code}: {detail}"
            ) from exc
        raise ModelProviderConnectionError(
            f"Provider endpoint '{request.url}' returned HTTP {exc.code}."
        ) from exc
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise ModelProviderConnectionError(
            f"Could not reach provider endpoint '{request.url}'."
        ) from exc

    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelProviderResponseError(
            f"Provider endpoint '{request.url}' returned invalid JSON."
        ) from exc


class MockModelProvider(ModelProvider):
    """Deterministic in-memory provider for unit and integration tests."""

    def __init__(
        self,
        *,
        responses: Mapping[str, str] | None = None,
        response_factory: Callable[[ModelGenerationRequest], str] | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._response_factory = response_factory or _default_mock_response
        self.requests: list[ModelGenerationRequest] = []

    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        self.requests.append(request)
        text = self._responses.get(request.model_id)
        if text is None:
            text = self._response_factory(request)
        return ModelGenerationResult(model_id=request.model_id, text=text)


class _OpenAICompatibleModelProvider(ModelProvider):
    """One HTTP adapter type for OpenAI-compatible chat-completions endpoints.

    This protocol is intentionally confined to this adapter. Other providers
    can implement ``ModelProvider`` directly through a native SDK or local
    inference runtime without adopting this request/response shape.
    """

    def __init__(
        self,
        provider_config: ModelProviderConfig,
        model_configs: Iterable[ModelConfig],
        *,
        transport: Transport | None = None,
    ) -> None:
        if not provider_config.enabled:
            raise ModelProviderConfigurationError(
                f"Provider '{provider_config.id}' is disabled."
            )
        if not provider_config.endpoint:
            raise ModelProviderConfigurationError(
                "OpenAI-compatible providers require a configured endpoint."
            )
        self._provider_config = provider_config
        self._provider_models = _build_provider_model_map(
            provider_config.id,
            model_configs,
        )
        if not self._provider_models:
            raise ModelProviderConfigurationError(
                f"Provider '{provider_config.id}' has no configured models."
            )
        self._transport = transport or _default_transport

    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        provider_model_id = self._provider_models.get(request.model_id)
        if provider_model_id is None:
            raise ModelProviderConfigurationError(
                f"Model '{request.model_id}' is not configured for provider "
                f"'{self._provider_config.id}'."
            )

        transport_request = _TransportRequest(
            url=_chat_completions_url(self._provider_config.endpoint),
            headers=self._build_headers(),
            payload=self._build_payload(request, provider_model_id),
            timeout_seconds=self._provider_config.timeout_seconds,
        )

        try:
            response_payload = self._transport(transport_request)
        except ModelProviderError:
            raise
        except (urllib_error.URLError, TimeoutError, OSError) as exc:
            raise ModelProviderConnectionError(
                f"Could not reach provider endpoint '{transport_request.url}'."
            ) from exc

        text = self._extract_text(response_payload)
        return ModelGenerationResult(model_id=request.model_id, text=text)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._provider_config.api_key_env_var:
            api_key = os.getenv(self._provider_config.api_key_env_var)
            if not api_key:
                raise ModelProviderConfigurationError(
                    "Configured provider api_key_env_var is not set in the environment."
                )
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _build_payload(
        request: ModelGenerationRequest,
        provider_model_id: str,
    ) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if request.system_prompt is not None:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        return {
            "model": provider_model_id,
            "messages": messages,
            "stream": False,
            "temperature": 0,
        }

    @staticmethod
    def _extract_text(response_payload: object) -> str:
        if not isinstance(response_payload, Mapping):
            raise ModelProviderResponseError(
                "OpenAI-compatible response must be a JSON object."
            )

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProviderResponseError(
                "OpenAI-compatible response must include at least one choice."
            )

        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ModelProviderResponseError(
                "OpenAI-compatible response choice must be an object."
            )

        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelProviderResponseError(
                "OpenAI-compatible response choice must include a message object."
            )

        text = _coerce_message_text(message.get("content"))
        if not text.strip():
            raise ModelProviderResponseError(
                "OpenAI-compatible response message content must not be empty."
            )
        return text


class LocalModelProvider(_OpenAICompatibleModelProvider):
    """Local OpenAI-compatible provider adapter, suitable for Ollama-style endpoints."""

    def __init__(
        self,
        provider_config: ModelProviderConfig,
        model_configs: Iterable[ModelConfig],
        *,
        transport: Transport | None = None,
    ) -> None:
        if provider_config.kind != "local":
            raise ModelProviderConfigurationError(
                f"LocalModelProvider requires a local provider config, got '{provider_config.kind}'."
            )
        super().__init__(provider_config, model_configs, transport=transport)


class APIModelProvider(_OpenAICompatibleModelProvider):
    """Temporary OpenAI-compatible API adapter for development/testing only."""

    def __init__(
        self,
        provider_config: ModelProviderConfig,
        model_configs: Iterable[ModelConfig],
        runtime_settings: RuntimeSettings,
        *,
        transport: Transport | None = None,
    ) -> None:
        if provider_config.kind != "api":
            raise ModelProviderConfigurationError(
                f"APIModelProvider requires an api provider config, got '{provider_config.kind}'."
            )
        if runtime_settings.environment == "production":
            raise TemporaryAPIProviderDisabledError(
                "Temporary API providers are not allowed in production."
            )
        if not runtime_settings.allow_temporary_api_provider:
            raise TemporaryAPIProviderDisabledError(
                "Temporary API providers are disabled by runtime configuration."
            )
        super().__init__(provider_config, model_configs, transport=transport)
