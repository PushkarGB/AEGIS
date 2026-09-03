"""Integration tests demonstrating provider substitution across the Agent invocation path.

Proves that the Agent-facing model invocation path can switch between:
- MockModelProvider;
- LocalModelProvider;
- APIModelProvider;

without changing Agent, Controller, or Broker logic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityMetadata,
    CapabilityRegistry,
)
from aegis.config import (
    CapabilityRegistryConfig,
    ModelConfig,
    ModelProviderConfig,
    ModelRegistryConfig,
    RuntimeSettings,
    load_config,
)
from aegis.orchestration import ExecutionController, ExecutionEventKind, WorkflowName
from aegis.router import (
    APIModelProvider,
    LocalModelProvider,
    MockModelProvider,
    ModelGenerationRequest,
    ModelGenerationResult,
    ModelProvider,
    ModelProviderConnectionError,
    ModelRegistry,
    ModelRouter,
    TemporaryAPIProviderDisabledError,
)
from aegis.schemas import (
    AgentDecision,
    Artifact,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    Observation,
    TaskState,
    VerificationStatus,
)


# ── Test Capabilities ────────────────────────────────────────────────


class StubCapability(Capability):
    """Deterministic capability implementation for integration testing."""

    def __init__(
        self,
        name: str,
        kind: CapabilityKind,
        *,
        handler: Any = None,
    ) -> None:
        self._metadata = CapabilityMetadata(
            name=name,
            kind=kind,
            description=f"Test capability for {name}.",
        )
        self.handler = handler
        self.invocations: list[CapabilityRequest] = []

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        self.invocations.append(request)
        if self.handler is not None:
            return self.handler(request)
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"executed": self.metadata.name},
        )


def _build_test_broker(
    *, fail_code_execution_once: bool = False
) -> tuple[RegistryCapabilityBroker, dict[str, StubCapability]]:
    """Build a broker with registered capabilities matching the computation workflow."""

    code_run_count = 0

    def handle_run_code(req: CapabilityRequest) -> CapabilityResult:
        nonlocal code_run_count
        code_run_count += 1
        if fail_code_execution_once and code_run_count == 1:
            return CapabilityResult(
                request_id=req.request_id,
                status=CapabilityResultStatus.FAILED,
                error="ZeroDivisionError: division by zero in generated calculation.",
                observations=[
                    Observation(
                        source="sandbox",
                        kind="execution_error",
                        summary="ZeroDivisionError on line 4 of generated script.",
                        data={"exit_code": 1, "stderr": "ZeroDivisionError"},
                        request_id=req.request_id,
                    )
                ],
            )
        return CapabilityResult(
            request_id=req.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"result": "thickness_check_passed", "min_reading": 4.2},
            observations=[
                Observation(
                    source="sandbox",
                    kind="computation_result",
                    summary="All 10 equipment items evaluated; item E-104 below threshold.",
                    data={"below_threshold": ["E-104"], "average_thickness": 5.8},
                    request_id=req.request_id,
                )
            ],
        )

    def handle_inspect(req: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(
            request_id=req.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"columns": ["equipment_id", "thickness", "min_thickness"], "rows": 10},
            observations=[
                Observation(
                    source="inspect_spreadsheet",
                    kind="schema_inspected",
                    summary="Spreadsheet contains 10 equipment thickness records.",
                    data={"columns": ["equipment_id", "thickness", "min_thickness"]},
                    request_id=req.request_id,
                )
            ],
        )

    def handle_verify(req: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(
            request_id=req.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"verified": True, "criteria": "min_thickness_rule"},
            observations=[
                Observation(
                    source="verify_result",
                    kind="verification_passed",
                    summary="Calculation verified against engineering tolerance rules.",
                    request_id=req.request_id,
                )
            ],
        )

    def handle_generate_excel(req: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(
            request_id=req.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"deliverable": "equipment_thickness_report.xlsx"},
            artifacts=[
                Artifact(
                    name="equipment_thickness_report.xlsx",
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    location="artifacts/equipment_thickness_report.xlsx",
                    description="Calculated thickness report.",
                    source_request_id=req.request_id,
                )
            ],
        )

    capabilities = {
        "inspect_spreadsheet": StubCapability(
            "inspect_spreadsheet", CapabilityKind.TOOL, handler=handle_inspect
        ),
        "generate_code": StubCapability("generate_code", CapabilityKind.MODEL),
        "run_code": StubCapability("run_code", CapabilityKind.TOOL, handler=handle_run_code),
        "verify_result": StubCapability("verify_result", CapabilityKind.TOOL, handler=handle_verify),
        "generate_excel": StubCapability(
            "generate_excel", CapabilityKind.TOOL, handler=handle_generate_excel
        ),
        "finish": StubCapability("finish", CapabilityKind.CONTROL),
        "analyze_image": StubCapability("analyze_image", CapabilityKind.MODEL),
    }

    registry = CapabilityRegistry(load_config().capabilities)
    for cap in capabilities.values():
        registry.register(cap)

    return RegistryCapabilityBroker(registry), capabilities


# ── Recording HTTP Transport for Mocked OpenAI-Compatible Calls ──────


class RecordingHTTPTransport:
    """Mocked transport recording outgoing HTTP requests and returning configured responses."""

    def __init__(self, response_map: Mapping[str, str] | None = None) -> None:
        self._response_map = dict(response_map or {})
        self.recorded_requests: list[Any] = []

    def set_response(self, model_or_key: str, content: str) -> None:
        self._response_map[model_or_key] = content

    def __call__(self, request: Any) -> object:
        self.recorded_requests.append(request)
        model = request.payload.get("model", "")
        # Find matching content or return default
        content = self._response_map.get(model)
        if content is None:
            # Check for substring match in prompts
            messages = request.payload.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""
            for key, val in self._response_map.items():
                if key in user_msg:
                    content = val
                    break
        if content is None:
            content = f"openai-response:{model}"

        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }


# ── Agent Consumer: Agent-Facing Model Invocation Path ────────────────


class AgentModelConsumer:
    """Represents the Agent intelligence that invokes models to propose decisions.

    CRITICAL ARCHITECTURAL PROPERTY:
    This class interacts ONLY with ``ModelRouter`` and the abstract ``ModelProvider``
    interface (or a mapping of provider IDs to ``ModelProvider``).
    It contains ZERO provider-specific code (no Ollama logic, no API logic, no mock logic).
    """

    def __init__(
        self,
        router: ModelRouter,
        provider: ModelProvider | Mapping[str, ModelProvider],
    ) -> None:
        self._router = router
        self._provider = provider
        self.generation_history: list[tuple[ModelGenerationRequest, ModelGenerationResult]] = []

    def _resolve_provider(self, provider_id: str) -> ModelProvider:
        if isinstance(self._provider, Mapping):
            if provider_id not in self._provider:
                raise KeyError(f"No provider instance configured for '{provider_id}'.")
            return self._provider[provider_id]
        return self._provider

    def _invoke_model(self, task_type: str, prompt: str, system_prompt: str | None = None) -> str:
        """Route to the appropriate model and generate text via the resolved provider."""
        decision = self._router.route(task_type)
        provider = self._resolve_provider(decision.provider_id)
        request = ModelGenerationRequest(
            model_id=decision.model_id,
            prompt=prompt,
            system_prompt=system_prompt,
        )
        result = provider.generate(request)
        self.generation_history.append((request, result))
        return result.text

    def propose_next_action(self, state: TaskState) -> AgentDecision:
        """Propose the next structured AgentDecision based on current TaskState.

        Demonstrates the ACT -> OBSERVE -> REASON -> ACT cycle:
        The Agent reads state observations, asks the routed model what to do,
        and constructs an AgentDecision for the ExecutionController to govern.
        """
        step = state.current_step

        if step == "inspect":
            response = self._invoke_model(
                task_type="general_reasoning",
                prompt=f"Goal: {state.user_goal}. What action should be taken first?",
                system_prompt="Return the action name to start.",
            )
            # Both mock and OpenAI-compatible adapters can return structured or text answers
            action = "inspect_spreadsheet" if "inspect" in response else "inspect_spreadsheet"
            return AgentDecision(
                action=action,
                inputs={"workbook": "readings.xlsx"},
                summary=f"Model proposed {action}.",
            )

        if step == "generate":
            # Check if recovering from a previous failure
            error_obs = [obs for obs in state.observations if obs.kind == "execution_error"]
            prompt = (
                f"Error encountered: {error_obs[-1].summary}. Regenerate corrected calculation code."
                if error_obs
                else f"Based on inspected schema, generate calculation code for goal: {state.user_goal}."
            )
            code_text = self._invoke_model(
                task_type="code_generation",
                prompt=prompt,
                system_prompt="Generate executable Python calculation code.",
            )
            return AgentDecision(
                action="generate_code",
                inputs={"code": code_text},
                summary="Model generated computation code.",
            )

        if step == "run":
            return AgentDecision(
                action="run_code",
                inputs={},
                summary="Propose executing generated code in sandbox.",
            )

        if step == "verify":
            return AgentDecision(
                action="verify_result",
                inputs={"tolerance": 0.01},
                summary="Propose verifying computation result.",
            )

        if step == "deliver":
            return AgentDecision(
                action="generate_excel",
                inputs={"template": "report.xlsx"},
                summary="Propose generating deliverable Excel workbook.",
            )

        if step == "finish":
            response = self._invoke_model(
                task_type="general_reasoning",
                prompt="Task deliverables verified. Confirm completion.",
                system_prompt="Confirm task finish.",
            )
            return AgentDecision(
                action="finish",
                done=True,
                summary=f"Model confirmed finish: {response[:30]}.",
            )

        raise ValueError(f"Unexpected workflow step: {step}")


# ── Helpers to Build Providers ───────────────────────────────────────


def _create_mock_provider() -> MockModelProvider:
    return MockModelProvider(
        responses={
            "agent_model_placeholder": "inspect_spreadsheet_action",
            "coding_model_placeholder": "def calculate(): return {'below': ['E-104']}",
        }
    )


def _create_local_provider(transport: RecordingHTTPTransport) -> LocalModelProvider:
    provider_config = ModelProviderConfig(
        id="local_stub",
        kind="local",
        enabled=True,
        endpoint="http://localhost:11434/v1",
        timeout_seconds=30,
    )
    model_configs = [
        ModelConfig(
            id="agent_model_placeholder",
            provider="local_stub",
            provider_model_id="qwen3:8b",
            roles=["agent"],
        ),
        ModelConfig(
            id="coding_model_placeholder",
            provider="local_stub",
            provider_model_id="qwen2.5-coder:7b",
            roles=["coding"],
        ),
        ModelConfig(
            id="vision_model_placeholder",
            provider="local_stub",
            provider_model_id="qwen2-vl:7b",
            roles=["vision"],
        ),
    ]
    return LocalModelProvider(provider_config, model_configs, transport=transport)


def _create_api_provider(
    transport: RecordingHTTPTransport, monkeypatch: pytest.MonkeyPatch
) -> APIModelProvider:
    monkeypatch.setenv("AEGIS_INTEGRATION_TEST_KEY", "test-bearer-token")
    provider_config = ModelProviderConfig(
        id="temporary_api_stub",
        kind="api",
        enabled=True,
        endpoint="https://api.example.invalid/v1",
        timeout_seconds=30,
        api_key_env_var="AEGIS_INTEGRATION_TEST_KEY",
    )
    model_configs = [
        ModelConfig(
            id="agent_model_placeholder",
            provider="temporary_api_stub",
            provider_model_id="gpt-4o-mini-test",
            roles=["agent"],
        ),
        ModelConfig(
            id="coding_model_placeholder",
            provider="temporary_api_stub",
            provider_model_id="gpt-4o-coder-test",
            roles=["coding"],
        ),
        ModelConfig(
            id="vision_model_placeholder",
            provider="temporary_api_stub",
            provider_model_id="gpt-4o-vision-test",
            roles=["vision"],
        ),
    ]
    runtime_settings = RuntimeSettings(
        environment="testing",
        allow_temporary_api_provider=True,
    )
    return APIModelProvider(
        provider_config,
        model_configs,
        runtime_settings,
        transport=transport,
    )


def _create_standard_router(provider_id: str = "local_stub") -> ModelRouter:
    registry_config = ModelRegistryConfig(
        providers=[
            ModelProviderConfig(
                id=provider_id,
                kind="local" if "local" in provider_id else "api",
                endpoint="http://localhost:11434/v1",
            )
        ],
        models=[
            ModelConfig(
                id="agent_model_placeholder",
                provider=provider_id,
                roles=["agent"],
                capabilities=["text_generation", "reasoning"],
                task_types=["general_reasoning", "drafting"],
            ),
            ModelConfig(
                id="coding_model_placeholder",
                provider=provider_id,
                roles=["coding"],
                capabilities=["text_generation", "code_generation"],
                task_types=["code_generation"],
            ),
            ModelConfig(
                id="vision_model_placeholder",
                provider=provider_id,
                roles=["vision"],
                capabilities=["text_generation", "image_understanding"],
                task_types=["visual_reasoning", "image_analysis"],
            ),
        ],
        role_defaults={
            "agent": "agent_model_placeholder",
            "coding": "coding_model_placeholder",
            "vision": "vision_model_placeholder",
        },
    )
    return ModelRouter(ModelRegistry(registry_config))


# ── Core Integration Test 1: Full Workflow Execution under Substitution ──


@pytest.mark.parametrize(
    "provider_type",
    ["mock", "local", "api"],
)
def test_full_workflow_execution_with_provider_substitution(
    provider_type: str, monkeypatch: pytest.MonkeyPatch
):
    """Prove that MockModelProvider, LocalModelProvider, and APIModelProvider

    can drive the exact same Agent consumer through the exact same Controller
    and Broker workflow to successful completion without changing any
    Agent, Controller, or Broker code.
    """
    transport = RecordingHTTPTransport(
        {
            "qwen3:8b": "inspect_spreadsheet_action",
            "qwen2.5-coder:7b": "def calc(): return {'below': ['E-104']}",
            "gpt-4o-mini-test": "inspect_spreadsheet_action",
            "gpt-4o-coder-test": "def calc(): return {'below': ['E-104']}",
        }
    )

    # 1. Instantiate the specific provider under test
    provider_id = f"{provider_type}_stub"
    if provider_type == "mock":
        provider: ModelProvider = _create_mock_provider()
    elif provider_type == "local":
        provider = _create_local_provider(transport)
    elif provider_type == "api":
        provider = _create_api_provider(transport, monkeypatch)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")

    # 2. Wire router, agent consumer, broker, and controller
    router = _create_standard_router(provider_id=provider_id if provider_type != "mock" else "local_stub")
    agent = AgentModelConsumer(router, provider)
    broker, _ = _build_test_broker()

    state = TaskState(
        user_goal="Calculate average measured thickness and identify equipment below minimum.",
        attachments=["readings.xlsx"],
        intent="computation",
        modality="spreadsheet",
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    # 3. Run the complete ACT -> OBSERVE -> REASON loop until completion
    max_steps = 10
    step_count = 0
    while state.final_status == FinalStatus.NOT_FINAL and step_count < max_steps:
        decision = agent.propose_next_action(state)
        controller.execute(decision)
        step_count += 1

    # 4. Verify identical successful completion across all three providers
    assert state.final_status == FinalStatus.COMPLETED
    assert state.verification_status == VerificationStatus.PASSED
    assert state.completed_steps == [
        "inspect_spreadsheet",
        "generate_code",
        "run_code",
        "verify_result",
        "generate_excel",
        "finish",
    ]
    assert len(state.generated_artifacts) == 1
    assert state.generated_artifacts[0].name == "equipment_thickness_report.xlsx"

    # Verify Controller emitted expected event progression
    event_kinds = [event.kind for event in controller.execution_events]
    assert event_kinds[0] == ExecutionEventKind.TASK_STARTED
    assert event_kinds[-1] == ExecutionEventKind.TASK_COMPLETED
    assert ExecutionEventKind.TASK_COMPLETED in event_kinds

    # Verify model invocations occurred through the provider
    assert len(agent.generation_history) >= 2  # At least inspect + generate_code + finish

    # Verify transport specifics for non-mock providers
    if provider_type == "local":
        assert len(transport.recorded_requests) >= 2
        for req in transport.recorded_requests:
            assert req.url == "http://localhost:11434/v1/chat/completions"
            assert "messages" in req.payload
            assert "Authorization" not in req.headers
    elif provider_type == "api":
        assert len(transport.recorded_requests) >= 2
        for req in transport.recorded_requests:
            assert req.url == "https://api.example.invalid/v1/chat/completions"
            assert req.headers["Authorization"] == "Bearer test-bearer-token"
    elif provider_type == "mock":
        assert len(provider.requests) >= 2


# ── Integration Test 2: Dynamic Mid-Task Provider Substitution ────────


def test_dynamic_mid_task_provider_substitution(monkeypatch: pytest.MonkeyPatch):
    """Prove that the provider backing the Agent can be swapped mid-task

    (e.g. step 1 mock -> step 2 local -> step 3 api) without resetting
    or disrupting Controller state, Broker capabilities, or workflow rules.
    """
    transport_local = RecordingHTTPTransport({"qwen2.5-coder:7b": "def calc(): return True"})
    transport_api = RecordingHTTPTransport({"gpt-4o-mini-test": "confirmed finish"})

    mock_provider = _create_mock_provider()
    local_provider = _create_local_provider(transport_local)
    api_provider = _create_api_provider(transport_api, monkeypatch)

    router = _create_standard_router("local_stub")
    broker, _ = _build_test_broker()

    state = TaskState(
        user_goal="Calculate equipment thickness.",
        attachments=["readings.xlsx"],
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    # Step 1: Agent powered by MockModelProvider
    agent_mock = AgentModelConsumer(router, mock_provider)
    decision_1 = agent_mock.propose_next_action(state)
    controller.execute(decision_1)
    assert state.current_step == "generate"
    assert len(mock_provider.requests) == 1

    # Step 2: Swap to LocalModelProvider for code generation
    agent_local = AgentModelConsumer(router, local_provider)
    decision_2 = agent_local.propose_next_action(state)
    controller.execute(decision_2)
    assert state.current_step == "run"
    assert len(transport_local.recorded_requests) == 1

    # Step 3-5: Run code, verify, deliver
    decision_3 = agent_local.propose_next_action(state)  # run_code
    controller.execute(decision_3)
    decision_4 = agent_local.propose_next_action(state)  # verify_result
    controller.execute(decision_4)
    decision_5 = agent_local.propose_next_action(state)  # generate_excel
    controller.execute(decision_5)
    assert state.current_step == "finish"

    # Step 6: Swap to APIModelProvider for finish confirmation
    agent_api = AgentModelConsumer(router, api_provider)
    decision_6 = agent_api.propose_next_action(state)
    controller.execute(decision_6)

    # Workflow successfully completed across all 3 providers
    assert state.final_status == FinalStatus.COMPLETED
    assert len(transport_api.recorded_requests) == 1
    assert state.completed_steps == [
        "inspect_spreadsheet",
        "generate_code",
        "run_code",
        "verify_result",
        "generate_excel",
        "finish",
    ]


# ── Integration Test 3: Heterogeneous Provider Dispatch via ModelRouter ─


def test_heterogeneous_provider_dispatch_via_router(monkeypatch: pytest.MonkeyPatch):
    """Prove that a single Agent session can dispatch to multiple heterogeneous providers

    (Mock for agent role, Local for coding role, API for vision role)
    selected deterministically by ModelRouter without modifying Agent or Controller logic.
    """
    transport_local = RecordingHTTPTransport({"local-coding": "print('local code')"})
    transport_api = RecordingHTTPTransport({"api-vision": "visible rust detected"})

    mock_prov = MockModelProvider(responses={"mock-agent": "proceed with task"})

    local_prov = LocalModelProvider(
        ModelProviderConfig(id="local_p", kind="local", endpoint="http://localhost:11434/v1"),
        [ModelConfig(id="mock-coding", provider="local_p", provider_model_id="local-coding", roles=["coding"])],
        transport=transport_local,
    )

    monkeypatch.setenv("AEGIS_VISION_KEY", "vision-api-key")
    api_prov = APIModelProvider(
        ModelProviderConfig(
            id="api_p",
            kind="api",
            endpoint="https://vision.api/v1",
            api_key_env_var="AEGIS_VISION_KEY",
        ),
        [ModelConfig(id="mock-vision", provider="api_p", provider_model_id="api-vision", roles=["vision"])],
        RuntimeSettings(environment="testing", allow_temporary_api_provider=True),
        transport=transport_api,
    )

    registry_config = ModelRegistryConfig(
        providers=[
            ModelProviderConfig(id="mock_p", kind="mock"),
            ModelProviderConfig(id="local_p", kind="local", endpoint="http://localhost:11434/v1"),
            ModelProviderConfig(id="api_p", kind="api", endpoint="https://vision.api/v1"),
        ],
        models=[
            ModelConfig(id="mock-agent", provider="mock_p", roles=["agent"], task_types=["general_reasoning"]),
            ModelConfig(id="mock-coding", provider="local_p", roles=["coding"], task_types=["code_generation"]),
            ModelConfig(id="mock-vision", provider="api_p", roles=["vision"], task_types=["visual_reasoning"]),
        ],
        role_defaults={
            "agent": "mock-agent",
            "coding": "mock-coding",
            "vision": "mock-vision",
        },
    )
    router = ModelRouter(ModelRegistry(registry_config))

    # Provider map mapping provider_id -> ModelProvider
    provider_map: dict[str, ModelProvider] = {
        "mock_p": mock_prov,
        "local_p": local_prov,
        "api_p": api_prov,
    }

    agent = AgentModelConsumer(router, provider_map)

    # Agent invokes general reasoning -> routed to mock_p
    res_agent = agent._invoke_model("general_reasoning", "Plan the next step.")
    assert res_agent == "proceed with task"
    assert len(mock_prov.requests) == 1

    # Agent invokes code generation -> routed to local_p
    res_code = agent._invoke_model("code_generation", "Generate Python function.")
    assert res_code == "print('local code')"
    assert len(transport_local.recorded_requests) == 1

    # Agent invokes visual reasoning -> routed to api_p
    res_vision = agent._invoke_model("visual_reasoning", "Analyze pipe image.")
    assert res_vision == "visible rust detected"
    assert len(transport_api.recorded_requests) == 1


# ── Integration Test 4: Agentic Self-Correction under All Providers ──


@pytest.mark.parametrize("provider_type", ["mock", "local", "api"])
def test_agentic_recovery_loop_preserves_substitution_on_failure(
    provider_type: str, monkeypatch: pytest.MonkeyPatch
):
    """Prove that the agentic error recovery loop:

        ACT -> OBSERVE ERROR -> REASON -> CORRECT -> ACT
    functions identically across Mock, Local, and API providers,
    with the Controller enforcing retry limits without leaking provider details.
    """
    transport = RecordingHTTPTransport(
        {
            "qwen3:8b": "inspect_spreadsheet",
            "qwen2.5-coder:7b": "def fixed_calc(): return True",
            "gpt-4o-mini-test": "inspect_spreadsheet",
            "gpt-4o-coder-test": "def fixed_calc(): return True",
        }
    )

    provider_id = f"{provider_type}_stub"
    if provider_type == "mock":
        provider: ModelProvider = _create_mock_provider()
    elif provider_type == "local":
        provider = _create_local_provider(transport)
    elif provider_type == "api":
        provider = _create_api_provider(transport, monkeypatch)
    else:
        raise ValueError(provider_type)

    router = _create_standard_router(provider_id=provider_id if provider_type != "mock" else "local_stub")
    agent = AgentModelConsumer(router, provider)

    # Broker configured to fail run_code on the first attempt
    broker, _ = _build_test_broker(fail_code_execution_once=True)

    state = TaskState(
        user_goal="Calculate thickness with resilience check.",
        attachments=["readings.xlsx"],
        max_iterations=10,
        max_retries=2,
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    # 1. Step 1: inspect
    controller.execute(agent.propose_next_action(state))
    assert state.current_step == "generate"

    # 2. Step 2: generate_code (first attempt)
    controller.execute(agent.propose_next_action(state))
    assert state.current_step == "run"

    # 3. Step 3: run_code fails
    event_fail = controller.execute(agent.propose_next_action(state))
    assert event_fail.kind == ExecutionEventKind.ACTION_FAILED
    assert state.current_step == "generate"  # Controller transitioned back to generate!
    assert state.retry_count == 1
    assert any(obs.kind == "execution_error" for obs in state.observations)

    # 4. Step 4: Agent reads the error observation, generates corrected code
    controller.execute(agent.propose_next_action(state))
    assert state.current_step == "run"

    # 5. Step 5: run_code succeeds on second attempt
    event_ok = controller.execute(agent.propose_next_action(state))
    assert event_ok.kind == ExecutionEventKind.ACTION_COMPLETED
    assert state.current_step == "verify"

    # 6. Complete remaining steps
    controller.execute(agent.propose_next_action(state))  # verify_result
    controller.execute(agent.propose_next_action(state))  # generate_excel
    controller.execute(agent.propose_next_action(state))  # finish

    assert state.final_status == FinalStatus.COMPLETED
    assert state.retry_count == 1


# ── Integration Test 5: Provider Failures Isolated from Controller State ─


def test_provider_connection_failure_leaves_controller_state_uncorrupted():
    """Verify that a provider-level connection error (e.g. endpoint down)

    raises cleanly at the Agent invocation boundary and prevents illegal
    unvalidated actions from reaching or corrupting the Controller.
    """
    def failing_transport(_: Any) -> Any:
        raise OSError("Connection refused to Ollama daemon at 127.0.0.1:11434")

    local_provider = LocalModelProvider(
        ModelProviderConfig(id="local_stub", kind="local", endpoint="http://localhost:11434/v1"),
        [ModelConfig(id="agent_model_placeholder", provider="local_stub", roles=["agent"])],
        transport=failing_transport,
    )
    router = _create_standard_router("local_stub")
    agent = AgentModelConsumer(router, local_provider)
    broker, _ = _build_test_broker()

    state = TaskState(user_goal="Test resilience under provider outage.")
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    # Attempting to generate action when provider is down raises ModelProviderConnectionError
    with pytest.raises(ModelProviderConnectionError, match="Could not reach provider endpoint"):
        agent.propose_next_action(state)

    # Controller state was NOT corrupted: still at start step, only initialization events, not terminal
    assert state.current_step == "inspect"
    assert state.final_status == FinalStatus.NOT_FINAL
    assert len(controller.execution_events) == 2


def test_temporary_api_policy_enforced_before_controller_execution():
    """Verify that temporary API providers are strictly blocked in production

    before any Agent proposal can execute on the Controller.
    """
    prod_settings = RuntimeSettings(environment="production", allow_temporary_api_provider=False)
    provider_config = ModelProviderConfig(id="api_stub", kind="api", endpoint="https://api.example.com/v1")
    model_configs = [ModelConfig(id="agent_model_placeholder", provider="api_stub", roles=["agent"])]

    with pytest.raises(TemporaryAPIProviderDisabledError, match="not allowed in production"):
        APIModelProvider(provider_config, model_configs, prod_settings)
