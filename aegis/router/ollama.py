"""Ollama HTTP provider adapter beneath the provider-neutral ModelProvider interface.

Connects the AEGIS ModelProvider layer to a configurable Ollama endpoint
via Ollama's native HTTP API (/api/chat, /api/generate) and OpenAI-compatible
endpoint (/v1/chat/completions), with health monitoring, Qwen3.5 tag support,
and non-thinking mode preservation.
"""

from __future__ import annotations

import json
import os
import re
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, ConfigDict, Field

from aegis.config import ModelConfig, ModelHealth, ModelProviderConfig, OllamaConfig, load_ollama_config

from .provider import ModelGenerationRequest, ModelGenerationResult, ModelProvider
from .providers import (
    ModelProviderConfigurationError,
    ModelProviderConnectionError,
    ModelProviderError,
    ModelProviderResponseError,
    _build_provider_model_map,
)


@dataclass(frozen=True)
class OllamaHttpRequest:
    """HTTP request specification for the Ollama adapter."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    payload: dict[str, object] | None = None
    timeout_seconds: float = 60.0


OllamaTransport = Callable[[OllamaHttpRequest], object]


class OllamaHealthStatus(BaseModel):
    """Structured health check result for an Ollama endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: bool
    status: ModelHealth
    endpoint: str
    version: str | None = None
    available_models: list[str] = Field(default_factory=list)
    resolved_model: str | None = None
    error_message: str | None = None


def _default_ollama_transport(request: OllamaHttpRequest) -> object:
    """Default HTTP transport using urllib.request with robust error mapping."""
    body = json.dumps(request.payload).encode("utf-8") if request.payload is not None else None
    http_req = urllib_request.Request(
        request.url,
        data=body,
        headers=request.headers,
        method=request.method,
    )

    try:
        with urllib_request.urlopen(http_req, timeout=request.timeout_seconds) as response:
            raw = response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 404:
            raise ModelProviderResponseError(
                f"Ollama endpoint '{request.url}' returned HTTP 404: {detail or 'Not Found'}"
            ) from exc
        raise ModelProviderConnectionError(
            f"Ollama endpoint '{request.url}' returned HTTP {exc.code}: {detail or 'Server Error'}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ModelProviderConnectionError(
            f"Ollama request to '{request.url}' timed out after {request.timeout_seconds} seconds."
        ) from exc
    except urllib_error.URLError as exc:
        # Check if wrapped reason was a timeout
        if isinstance(exc.reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc.reason).lower():
            raise ModelProviderConnectionError(
                f"Ollama request to '{request.url}' timed out after {request.timeout_seconds} seconds."
            ) from exc
        raise ModelProviderConnectionError(
            f"Could not reach Ollama endpoint at '{request.url}': {exc.reason}"
        ) from exc
    except OSError as exc:
        raise ModelProviderConnectionError(
            f"Could not reach Ollama endpoint at '{request.url}': {exc}"
        ) from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelProviderResponseError(
            f"Ollama endpoint '{request.url}' returned invalid JSON."
        ) from exc


def _resolve_endpoints(base_url: str) -> tuple[str, bool]:
    """Resolve base URL to the appropriate chat completions URL and flag if OpenAI compatible."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed, True
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions", True
    if trimmed.endswith("/api/chat") or trimmed.endswith("/api/generate"):
        return trimmed, False
    return f"{trimmed}/api/chat", False


class OllamaModelProvider(ModelProvider):
    """Configurable Ollama adapter beneath the provider-neutral ModelProvider interface.

    Endpoint and timeout hierarchy:
    1. Explicit constructor arguments
    2. ModelProviderConfig endpoint/timeout
    3. Environment variables
    4. Sane local defaults

    Model-tag hierarchy:
    1. The routed registry model's ``provider_model_id``
    2. Explicit/environment ``OLLAMA_MODEL`` configuration for an unregistered model
    3. The requested model ID
    """

    def __init__(
        self,
        provider_config: ModelProviderConfig | None = None,
        model_configs: Iterable[ModelConfig] | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | float | None = None,
        non_thinking: bool | None = None,
        transport: OllamaTransport | None = None,
    ) -> None:
        if provider_config is not None:
            if not provider_config.enabled:
                raise ModelProviderConfigurationError(
                    f"Provider '{provider_config.id}' is disabled."
                )
            if provider_config.kind not in ("ollama", "local"):
                raise ModelProviderConfigurationError(
                    f"OllamaModelProvider requires kind 'ollama' or 'local', got '{provider_config.kind}'."
                )

        self._provider_config = provider_config
        if provider_config and model_configs:
            self._provider_models = _build_provider_model_map(
                provider_config.id, model_configs
            )
        elif model_configs:
            self._provider_models = {
                m.id: m.provider_model_id or m.id for m in model_configs
            }
        else:
            self._provider_models = {}

        resolved_base = (
            base_url
            or (provider_config.endpoint if provider_config and provider_config.endpoint else None)
        )
        resolved_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else (provider_config.timeout_seconds if provider_config else None)
        )

        self.config: OllamaConfig = load_ollama_config(
            base_url=resolved_base,
            model=model,
            timeout_seconds=resolved_timeout,
            non_thinking=non_thinking,
        )

        self._transport: OllamaTransport = transport or _default_ollama_transport
        self._available_models: list[str] = []

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def timeout_seconds(self) -> float:
        return self.config.timeout_seconds

    @property
    def non_thinking(self) -> bool:
        return self.config.non_thinking

    def resolve_model_tag(self, requested_model_id: str) -> str:
        """Resolve the model tag to send to the Ollama server.

        A configured provider-model mapping is authoritative for a routed
        registry model. ``OLLAMA_MODEL`` remains a single-model fallback for
        standalone or otherwise unregistered requests; it must not collapse
        configured agent, coding, and vision roles onto one tag.
        """
        if requested_model_id in self._provider_models:
            target_tag = self._provider_models[requested_model_id]
        else:
            env_model = os.getenv("OLLAMA_MODEL")
            if env_model:
                target_tag = env_model
            elif self.config.model:
                target_tag = self.config.model
            else:
                target_tag = requested_model_id

        if self._available_models:
            if target_tag in self._available_models:
                return target_tag
            if f"{target_tag}:latest" in self._available_models:
                return f"{target_tag}:latest"
            if target_tag.endswith(":latest") and target_tag[:-7] in self._available_models:
                return target_tag[:-7]
            for candidate in self._available_models:
                if candidate.startswith(target_tag):
                    return candidate

        return target_tag

    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        """Generate text through the configured Ollama HTTP endpoint."""
        model_tag = self.resolve_model_tag(request.model_id)
        chat_url, is_openai = _resolve_endpoints(self.config.base_url)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._provider_config and self._provider_config.api_key_env_var:
            api_key = os.getenv(self._provider_config.api_key_env_var)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        payload = self._build_request_payload(request, model_tag, is_openai)

        http_req = OllamaHttpRequest(
            url=chat_url,
            method="POST",
            headers=headers,
            payload=payload,
            timeout_seconds=self.config.timeout_seconds,
        )

        try:
            response_payload = self._transport(http_req)
        except ModelProviderError:
            raise
        except (urllib_error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ModelProviderConnectionError(
                f"Could not reach Ollama endpoint '{chat_url}': {exc}"
            ) from exc

        text = self._extract_text(response_payload)
        return ModelGenerationResult(model_id=request.model_id, text=text)

    def check_health(self) -> OllamaHealthStatus:
        """Check the health of the configured Ollama endpoint and retrieve available models."""
        base = self.config.base_url.rstrip("/")
        timeout = min(self.config.timeout_seconds, 10.0)

        # 1. Query tags for model discovery
        tags_url = f"{base}/api/tags" if not base.endswith("/v1") else f"{base}/models"
        version_url = f"{base}/api/version"

        discovered_version: str | None = None
        discovered_models: list[str] = []

        try:
            tags_req = OllamaHttpRequest(
                url=tags_url,
                method="GET",
                headers={"Accept": "application/json"},
                timeout_seconds=timeout,
            )
            tags_resp = self._transport(tags_req)
            if isinstance(tags_resp, Mapping):
                # Native /api/tags: {"models": [{"name": "...", "model": "..."}]}
                models_list = tags_resp.get("models")
                if isinstance(models_list, list):
                    for item in models_list:
                        if isinstance(item, Mapping):
                            name = item.get("name") or item.get("model")
                            if isinstance(name, str):
                                discovered_models.append(name)
                # OpenAI /v1/models: {"data": [{"id": "..."}]}
                data_list = tags_resp.get("data")
                if isinstance(data_list, list):
                    for item in data_list:
                        if isinstance(item, Mapping):
                            mid = item.get("id")
                            if isinstance(mid, str):
                                discovered_models.append(mid)
        except (ModelProviderConnectionError, ModelProviderResponseError):
            pass

        # 2. Query version if available
        try:
            ver_req = OllamaHttpRequest(
                url=version_url,
                method="GET",
                headers={"Accept": "application/json"},
                timeout_seconds=timeout,
            )
            ver_resp = self._transport(ver_req)
            if isinstance(ver_resp, Mapping) and isinstance(ver_resp.get("version"), str):
                discovered_version = ver_resp["version"]
        except (ModelProviderConnectionError, ModelProviderResponseError):
            pass

        # 3. If neither tags nor version succeeded, probe root URL
        if not discovered_models and not discovered_version:
            try:
                root_req = OllamaHttpRequest(
                    url=base or "http://127.0.0.1:11434",
                    method="GET",
                    headers={"Accept": "text/plain, application/json"},
                    timeout_seconds=timeout,
                )
                self._transport(root_req)
            except ModelProviderConnectionError as exc:
                return OllamaHealthStatus(
                    healthy=False,
                    status=ModelHealth.UNAVAILABLE,
                    endpoint=self.config.base_url,
                    error_message=str(exc),
                )
            except ModelProviderResponseError as exc:
                return OllamaHealthStatus(
                    healthy=False,
                    status=ModelHealth.DEGRADED,
                    endpoint=self.config.base_url,
                    error_message=str(exc),
                )

        self._available_models = discovered_models
        resolved = self.resolve_model_tag("agent_model")

        return OllamaHealthStatus(
            healthy=True,
            status=ModelHealth.HEALTHY,
            endpoint=self.config.base_url,
            version=discovered_version,
            available_models=discovered_models,
            resolved_model=resolved,
        )

    def _build_request_payload(
        self,
        request: ModelGenerationRequest,
        model_tag: str,
        is_openai_endpoint: bool,
    ) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if request.system_prompt is not None:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        if is_openai_endpoint:
            return {
                "model": model_tag,
                "messages": messages,
                "stream": False,
                "temperature": 0,
            }
        return {
            "model": model_tag,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0,
            },
        }

    def _extract_text(self, payload: object) -> str:
        if not isinstance(payload, Mapping):
            raise ModelProviderResponseError("Ollama response must be a JSON object.")

        if "error" in payload:
            raise ModelProviderResponseError(f"Ollama returned error: {payload['error']}")

        # 1. Native /api/chat: {"message": {"content": "..."}}
        if "message" in payload and isinstance(payload["message"], Mapping):
            content = payload["message"].get("content")
            if isinstance(content, str):
                return self._postprocess_text(content)

        # 2. Native /api/generate: {"response": "..."}
        if "response" in payload and isinstance(payload["response"], str):
            return self._postprocess_text(payload["response"])

        # 3. OpenAI /v1/chat/completions: {"choices": [{"message": {"content": "..."}}]}
        if "choices" in payload and isinstance(payload["choices"], list) and payload["choices"]:
            first_choice = payload["choices"][0]
            if isinstance(first_choice, Mapping) and "message" in first_choice:
                msg = first_choice["message"]
                if isinstance(msg, Mapping):
                    content = msg.get("content")
                    if isinstance(content, str):
                        return self._postprocess_text(content)

        raise ModelProviderResponseError(
            "Ollama response does not contain recognizable assistant content."
        )

    def _postprocess_text(self, text: str) -> str:
        """Strip thinking tags if non-thinking mode is enabled and validate content."""
        if self.config.non_thinking:
            text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)

        cleaned = text.strip()
        if not cleaned:
            raise ModelProviderResponseError("Ollama response content must not be empty.")
        return cleaned
