"""Unit and integration tests for OllamaModelProvider with fake local HTTP server.

Tests all provider behaviors:
- Configuration parsing and environment variable overrides
- Native /api/chat, /api/generate, and /v1/chat/completions protocols
- Qwen3.5 tag support and dynamic tag resolution
- Non-thinking mode preservation (<think>...</think> stripping)
- Comprehensive error handling: unreachable endpoint, timeout, malformed response
- Health check status reporting
- Full Agent → ModelRouter → ModelProvider → Ollama integration
- Marked manual integration test for live endpoint verification
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from aegis.agent import AttachmentDescriptor, IntentAnalysisRequest, RouterAgentRuntime
from aegis.config import (
    AgentConfig,
    ModelConfig,
    ModelHealth,
    ModelProviderConfig,
    ModelRegistryConfig,
    OllamaConfig,
    load_ollama_config,
)
from aegis.router import (
    ModelGenerationRequest,
    ModelGenerationResult,
    ModelProvider,
    ModelProviderConfigurationError,
    ModelProviderConnectionError,
    ModelProviderResponseError,
    ModelRegistry,
    ModelRouter,
    OllamaHealthStatus,
    OllamaModelProvider,
)


# ── Fake In-Process Local Ollama HTTP Server ──────────────────────────


class FakeOllamaHandler(BaseHTTPRequestHandler):
    """Deterministic in-process HTTP handler simulating an Ollama server."""

    # Handlers can override default response payload per test
    custom_responses: dict[str, Any] = {}
    recorded_requests: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress standard HTTP server logging to keep pytest output clean
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        FakeOllamaHandler.recorded_requests.append({
            "method": "GET",
            "path": self.path,
            "headers": dict(self.headers),
        })

        if path in FakeOllamaHandler.custom_responses:
            resp = FakeOllamaHandler.custom_responses[path]
            self._send_custom(resp)
            return

        if path == "/api/version":
            self._send_json(200, {"version": "0.1.32"})
        elif path in ("/api/tags", "/models"):
            self._send_json(200, {
                "models": [
                    {"name": "qwen2.5:7b", "model": "qwen2.5:7b"},
                    {"name": "qwen3.5:latest", "model": "qwen3.5:latest"},
                ]
            })
        elif path == "/":
            self._send_text(200, "Ollama is running")
        else:
            self._send_json(404, {"error": f"Endpoint '{path}' not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception:
            body = body_bytes.decode("utf-8", errors="replace")

        FakeOllamaHandler.recorded_requests.append({
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })

        if path in FakeOllamaHandler.custom_responses:
            resp = FakeOllamaHandler.custom_responses[path]
            self._send_custom(resp)
            return

        if path == "/api/chat":
            model = body.get("model", "qwen2.5:7b") if isinstance(body, dict) else "unknown"
            self._send_json(200, {
                "model": model,
                "message": {
                    "role": "assistant",
                    "content": '{"intent":"computation","modality":"spreadsheet","reason":"User requested calculation"}',
                },
                "done": True,
            })
        elif path == "/api/generate":
            model = body.get("model", "qwen2.5:7b") if isinstance(body, dict) else "unknown"
            self._send_json(200, {
                "model": model,
                "response": "generated text response from native generate",
                "done": True,
            })
        elif path in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(200, {
                "choices": [
                    {"message": {"role": "assistant", "content": "openai format response"}}
                ]
            })
        else:
            self._send_json(404, {"error": f"Endpoint '{path}' not found"})

    def _send_custom(self, resp: Any) -> None:
        if isinstance(resp, tuple) and len(resp) == 2:
            status_code, content = resp
            if isinstance(content, dict):
                self._send_json(status_code, content)
            elif isinstance(content, str):
                self._send_text(status_code, content)
        elif callable(resp):
            resp(self)

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, status: int, text: str) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_ollama_server() -> Generator[str, None, None]:
    """Start an ephemeral background HTTP server simulating Ollama."""
    FakeOllamaHandler.custom_responses.clear()
    FakeOllamaHandler.recorded_requests.clear()

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield base_url

    server.shutdown()
    server.server_close()
    FakeOllamaHandler.custom_responses.clear()
    FakeOllamaHandler.recorded_requests.clear()


# ── Configuration Tests ──────────────────────────────────────────────


def test_ollama_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT", "OLLAMA_NON_THINKING"):
        monkeypatch.delenv(env, raising=False)

    config = load_ollama_config()
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.model == "qwen2.5:7b"
    assert config.timeout_seconds == 60.0
    assert config.non_thinking is True


def test_ollama_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom-ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "120")
    monkeypatch.setenv("OLLAMA_NON_THINKING", "false")

    config = load_ollama_config()
    assert config.base_url == "http://custom-ollama:11434"
    assert config.model == "qwen3.5"
    assert config.timeout_seconds == 120.0
    assert config.non_thinking is False


def test_ollama_config_explicit_args_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-url:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "90")

    config = load_ollama_config(
        base_url="http://explicit-url:11434",
        model="qwen3.5",
        timeout_seconds=45.0,
        non_thinking=True,
    )
    assert config.base_url == "http://explicit-url:11434"
    assert config.model == "qwen3.5"
    assert config.timeout_seconds == 45.0
    assert config.non_thinking is True


def test_ollama_provider_implements_model_provider() -> None:
    provider = OllamaModelProvider(base_url="http://127.0.0.1:11434")
    assert isinstance(provider, ModelProvider)


def test_ollama_provider_rejects_invalid_kind() -> None:
    with pytest.raises(ModelProviderConfigurationError, match="requires kind 'ollama' or 'local'"):
        OllamaModelProvider(
            provider_config=ModelProviderConfig(id="test", kind="api")
        )


def test_ollama_provider_rejects_disabled_provider() -> None:
    with pytest.raises(ModelProviderConfigurationError, match="is disabled"):
        OllamaModelProvider(
            provider_config=ModelProviderConfig(id="test", kind="ollama", enabled=False)
        )


# ── Protocol & Generation Tests with Fake Server ─────────────────────


def test_ollama_native_chat_generation(fake_ollama_server: str) -> None:
    provider = OllamaModelProvider(base_url=fake_ollama_server, model="qwen2.5:7b")
    request = ModelGenerationRequest(
        model_id="agent_model",
        system_prompt="Return JSON only.",
        prompt="Analyze user request.",
    )

    result = provider.generate(request)

    assert isinstance(result, ModelGenerationResult)
    assert result.model_id == "agent_model"
    assert '"intent":"computation"' in result.text

    # Verify HTTP request sent to fake server
    assert len(FakeOllamaHandler.recorded_requests) == 1
    req = FakeOllamaHandler.recorded_requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/api/chat"
    assert req["body"]["model"] == "qwen2.5:7b"
    assert req["body"]["stream"] is False
    assert req["body"]["messages"][0] == {"role": "system", "content": "Return JSON only."}
    assert req["body"]["messages"][1] == {"role": "user", "content": "Analyze user request."}


def test_ollama_native_generate_endpoint(fake_ollama_server: str) -> None:
    provider = OllamaModelProvider(base_url=f"{fake_ollama_server}/api/generate")
    request = ModelGenerationRequest(
        model_id="coding_model",
        prompt="Write calculation code.",
    )

    result = provider.generate(request)
    assert result.text == "generated text response from native generate"

    assert len(FakeOllamaHandler.recorded_requests) == 1
    req = FakeOllamaHandler.recorded_requests[0]
    assert req["path"] == "/api/generate"


def test_ollama_openai_compatible_endpoint(fake_ollama_server: str) -> None:
    provider = OllamaModelProvider(base_url=f"{fake_ollama_server}/v1")
    request = ModelGenerationRequest(
        model_id="agent_model",
        prompt="Hello OpenAI format.",
    )

    result = provider.generate(request)
    assert result.text == "openai format response"

    assert len(FakeOllamaHandler.recorded_requests) == 1
    req = FakeOllamaHandler.recorded_requests[0]
    assert req["path"] == "/v1/chat/completions"


# ── Qwen3.5 Model Support & Server Tag Resolution ────────────────────


def test_qwen3_5_model_configuration(fake_ollama_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 7: Support current Qwen3.5 model through configuration."""
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5")
    provider = OllamaModelProvider(base_url=fake_ollama_server)

    request = ModelGenerationRequest(model_id="agent_model", prompt="Test Qwen3.5.")
    provider.generate(request)

    req = FakeOllamaHandler.recorded_requests[0]
    assert req["body"]["model"] == "qwen3.5"


def test_server_tag_resolution_does_not_assume_specific_tag(fake_ollama_server: str) -> None:
    """Requirement 7: Do not assume a specific Ollama tag if the configured server reports a different tag."""
    # Server reports "qwen3.5:latest" and "qwen2.5:7b"
    provider = OllamaModelProvider(base_url=fake_ollama_server, model="qwen3.5")

    # Before health check, uses configured "qwen3.5"
    assert provider.resolve_model_tag("agent_model") == "qwen3.5"

    # Run health check to discover server tags
    health = provider.check_health()
    assert health.healthy is True
    assert "qwen3.5:latest" in health.available_models
    assert "qwen2.5:7b" in health.available_models

    # Server tag "qwen3.5:latest" matched to target "qwen3.5"
    assert provider.resolve_model_tag("agent_model") == "qwen3.5:latest"

    # If user configures a completely different tag (e.g. "custom-tag-123"), it does not crash or enforce qwen3.5
    custom_provider = OllamaModelProvider(base_url=fake_ollama_server, model="custom-tag-123")
    custom_provider.check_health()
    # Retains the configured custom tag without error
    assert custom_provider.resolve_model_tag("agent_model") == "custom-tag-123"


# ── Non-thinking Mode Preservation ───────────────────────────────────


def test_non_thinking_mode_strips_think_tags(fake_ollama_server: str) -> None:
    """Requirement 8: Non-thinking mode strips <think>...</think> reasoning blocks."""
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        200,
        {
            "model": "qwen2.5:7b",
            "message": {
                "role": "assistant",
                "content": "<think>\nLet's analyze the input.\nIt looks good.\n</think>\nFinal answer text.",
            },
            "done": True,
        },
    )

    provider = OllamaModelProvider(base_url=fake_ollama_server, non_thinking=True)
    result = provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Think."))

    assert result.text == "Final answer text."
    assert "<think>" not in result.text
    assert "Let's analyze" not in result.text


def test_non_thinking_mode_disabled_preserves_think_tags(fake_ollama_server: str) -> None:
    """When non_thinking is False, reasoning blocks are preserved."""
    raw_content = "<think>\nPrivate chain of thought.\n</think>\nActual response."
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        200,
        {
            "model": "qwen2.5:7b",
            "message": {
                "role": "assistant",
                "content": raw_content,
            },
            "done": True,
        },
    )

    provider = OllamaModelProvider(base_url=fake_ollama_server, non_thinking=False)
    result = provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Think."))

    assert "<think>" in result.text
    assert "Private chain of thought" in result.text


# ── Health Check Tests ───────────────────────────────────────────────


def test_health_check_healthy(fake_ollama_server: str) -> None:
    provider = OllamaModelProvider(base_url=fake_ollama_server, model="qwen2.5:7b")
    status = provider.check_health()

    assert isinstance(status, OllamaHealthStatus)
    assert status.healthy is True
    assert status.status == ModelHealth.HEALTHY
    assert status.version == "0.1.32"
    assert "qwen2.5:7b" in status.available_models
    assert status.error_message is None


def test_health_check_unreachable() -> None:
    # Use a local port where nothing is listening
    provider = OllamaModelProvider(base_url="http://127.0.0.1:29999", timeout_seconds=1.0)
    status = provider.check_health()

    assert status.healthy is False
    assert status.status == ModelHealth.UNAVAILABLE
    assert status.error_message is not None
    assert any(k in status.error_message.lower() for k in ("could not reach", "refused", "timed out"))


# ── Error Handling Tests ─────────────────────────────────────────────


def test_unreachable_endpoint_raises_connection_error() -> None:
    """Requirement 9: Unreachable endpoint error handling."""
    provider = OllamaModelProvider(base_url="http://127.0.0.1:29999", timeout_seconds=1.0)
    with pytest.raises(ModelProviderConnectionError, match=r"(Could not reach Ollama endpoint|timed out)"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test."))


def test_timeout_raises_connection_error(fake_ollama_server: str) -> None:
    """Requirement 9: Timeout error handling."""

    def timeout_handler(handler: BaseHTTPRequestHandler) -> None:
        time.sleep(0.6)
        handler.send_response(200)
        handler.end_headers()

    FakeOllamaHandler.custom_responses["/api/chat"] = timeout_handler

    provider = OllamaModelProvider(base_url=fake_ollama_server, timeout_seconds=0.2)
    with pytest.raises(ModelProviderConnectionError, match="timed out"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test timeout."))


def test_malformed_json_response_raises_response_error(fake_ollama_server: str) -> None:
    """Requirement 9: Malformed response error handling."""
    FakeOllamaHandler.custom_responses["/api/chat"] = (200, "THIS IS NOT JSON {{{")

    provider = OllamaModelProvider(base_url=fake_ollama_server)
    with pytest.raises(ModelProviderResponseError, match="invalid JSON"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test malformed."))


def test_empty_content_raises_response_error(fake_ollama_server: str) -> None:
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        200,
        {
            "model": "qwen2.5:7b",
            "message": {"role": "assistant", "content": "   \n\t  "},
            "done": True,
        },
    )

    provider = OllamaModelProvider(base_url=fake_ollama_server)
    with pytest.raises(ModelProviderResponseError, match="must not be empty"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test empty."))


def test_server_error_payload_raises_response_error(fake_ollama_server: str) -> None:
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        200,
        {"error": "model 'qwen2.5:7b' not found, try pulling it first"},
    )

    provider = OllamaModelProvider(base_url=fake_ollama_server)
    with pytest.raises(ModelProviderResponseError, match="Ollama returned error: model 'qwen2.5:7b' not found"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test error."))


def test_http_404_raises_response_error(fake_ollama_server: str) -> None:
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        404,
        {"error": "model not found"},
    )

    provider = OllamaModelProvider(base_url=fake_ollama_server)
    with pytest.raises(ModelProviderResponseError, match="HTTP 404"):
        provider.generate(ModelGenerationRequest(model_id="agent_model", prompt="Test 404."))


# ── Full Agent → ModelRouter → ModelProvider → Ollama Integration ────


def test_agent_routes_through_ollama_provider(fake_ollama_server: str) -> None:
    """Requirements 5 & 6: Agent uses Agent → ModelRouter → ModelProvider → Ollama.

    Ensures Agent does not call Ollama directly; all traffic flows through ModelRouter and ModelProvider.
    """
    FakeOllamaHandler.custom_responses["/api/chat"] = (
        200,
        {
            "model": "qwen2.5:7b",
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "intent": "computation",
                    "modality": "spreadsheet",
                    "workflow": "computation",
                    "summary": "Request requires numeric analysis on workbook.",
                }),
            },
            "done": True,
        },
    )

    registry_config = ModelRegistryConfig(
        providers=[
            ModelProviderConfig(
                id="ollama_provider",
                kind="ollama",
                enabled=True,
                endpoint=fake_ollama_server,
            )
        ],
        models=[
            ModelConfig(
                id="agent_model",
                provider="ollama_provider",
                provider_model_id="qwen2.5:7b",
                roles=["agent"],
                capabilities=["text_generation", "reasoning", "planning", "drafting"],
            )
        ],
        role_defaults={"agent": "agent_model"},
    )

    ollama_provider = OllamaModelProvider(
        provider_config=registry_config.providers[0],
        model_configs=registry_config.models,
    )
    router = ModelRouter(ModelRegistry(registry_config))

    agent_config = AgentConfig(
        name="aegis-agent",
        description="AEGIS sovereign agent",
        default_model_role="agent",
        allowed_modalities=["spreadsheet", "scanned_document", "image"],
    )
    agent = RouterAgentRuntime(
        config=agent_config,
        router=router,
        providers={"ollama_provider": ollama_provider},
    )

    decision = agent.decide_intent(
        IntentAnalysisRequest(
            user_goal="Calculate the average measured thickness.",
            attachments=[AttachmentDescriptor(name="inspection.xlsx")],
        )
    )

    assert decision.intent.value == "computation"
    assert decision.modality.value == "spreadsheet"

    # Confirm the fake server received exactly the routing request via ModelProvider
    assert len(FakeOllamaHandler.recorded_requests) == 1
    req = FakeOllamaHandler.recorded_requests[0]
    assert req["path"] == "/api/chat"
    assert req["body"]["model"] == "qwen2.5:7b"


def test_routed_registry_models_use_distinct_ollama_tags(
    fake_ollama_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A global fallback must not override configured role-specific model tags."""
    monkeypatch.setenv("OLLAMA_MODEL", "global-fallback-model")

    registry_config = ModelRegistryConfig(
        providers=[
            ModelProviderConfig(
                id="ollama_provider",
                kind="ollama",
                enabled=True,
                endpoint=fake_ollama_server,
            )
        ],
        models=[
            ModelConfig(
                id="agent_model",
                provider="ollama_provider",
                provider_model_id="qwen3:8b",
                roles=["agent"],
                capabilities=["text_generation", "reasoning", "planning", "drafting"],
            ),
            ModelConfig(
                id="coding_model",
                provider="ollama_provider",
                provider_model_id="qwen2.5-coder:7b",
                roles=["coding"],
                capabilities=["text_generation", "code_generation"],
            ),
            ModelConfig(
                id="vision_model",
                provider="ollama_provider",
                provider_model_id="qwen3.5:latest",
                roles=["vision"],
                capabilities=["text_generation", "image_understanding"],
            ),
        ],
        role_defaults={
            "agent": "agent_model",
            "coding": "coding_model",
            "vision": "vision_model",
        },
    )
    provider = OllamaModelProvider(
        provider_config=registry_config.providers[0],
        model_configs=registry_config.models,
    )
    router = ModelRouter(ModelRegistry(registry_config))

    expected_tags = {
        "general_reasoning": "qwen3:8b",
        "code_generation": "qwen2.5-coder:7b",
        "visual_reasoning": "qwen3.5:latest",
    }
    for task_type, expected_tag in expected_tags.items():
        routing = router.route(task_type)
        provider.generate(
            ModelGenerationRequest(model_id=routing.model_id, prompt=f"Test {task_type}.")
        )
        assert FakeOllamaHandler.recorded_requests[-1]["body"]["model"] == expected_tag


# ── Clearly Marked Manual Integration Check for Real Endpoint ────────


@pytest.mark.manual
def test_manual_live_ollama_endpoint() -> None:
    """Manual integration check for testing against a real live Ollama endpoint.

    Requirement 11: Add one clearly marked manual integration check for the real endpoint.

    Skipped by default during regular automated pytest runs.
    To execute this test against a live Ollama endpoint:
        $env:AEGIS_RUN_MANUAL_OLLAMA_TEST = "1"
        $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"  # or your ngrok / Colab tunnel
        $env:OLLAMA_MODEL = "qwen3.5"                    # or your model tag
        pytest tests/test_ollama_provider.py -k test_manual_live_ollama_endpoint -v -s
    """
    if not os.getenv("AEGIS_RUN_MANUAL_OLLAMA_TEST"):
        pytest.skip(
            "Manual integration test skipped. Set AEGIS_RUN_MANUAL_OLLAMA_TEST=1 "
            "and configure OLLAMA_BASE_URL to execute against a live Ollama server."
        )

    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    timeout = float(os.getenv("OLLAMA_TIMEOUT", "60"))

    print(f"\n[MANUAL CHECK] Connecting to live Ollama endpoint: {base_url} (model={model}, timeout={timeout}s)")

    provider = OllamaModelProvider(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout,
        non_thinking=True,
    )

    # 1. Health check
    health = provider.check_health()
    print(f"[MANUAL CHECK] Health status: {health.status.value} (healthy={health.healthy})")
    print(f"[MANUAL CHECK] Version: {health.version}")
    print(f"[MANUAL CHECK] Available models: {health.available_models}")
    assert health.healthy is True, f"Ollama endpoint health check failed: {health.error_message}"

    # 2. Test generation
    request = ModelGenerationRequest(
        model_id="agent_model",
        system_prompt="You are an AEGIS AI assistant. Respond concisely.",
        prompt="Respond with 'AEGIS-OLLAMA-OK' and confirm non-thinking operation.",
    )

    start_time = time.monotonic()
    result = provider.generate(request)
    elapsed = time.monotonic() - start_time

    print(f"[MANUAL CHECK] Generation succeeded in {elapsed:.2f}s:")
    print(f"[MANUAL CHECK] Output: {result.text}")

    assert len(result.text.strip()) > 0
    assert "<think>" not in result.text, "Thinking tags were not stripped in non-thinking mode."
    print("[MANUAL CHECK] All live Ollama checks passed successfully!")
