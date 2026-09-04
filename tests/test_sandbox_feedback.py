"""Connect sandbox observations to the Agent and prove bounded correction.

Demonstrates ACT → OBSERVE ERROR → REASON → CORRECT → ACT under Controller
retry limits. The Agent never executes code or capabilities directly.
"""

from __future__ import annotations

import json

from aegis.agent import (
    AgentDirective,
    RouterAgentRuntime,
    SandboxObservationLoop,
)
from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    CapabilityKind,
    CapabilityRegistry,
    GenerateCodeCapability,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
)
from aegis.capabilities.base import Capability, CapabilityMetadata
from aegis.config import load_config
from aegis.orchestration import ExecutionController, ExecutionEventKind, WorkflowName
from aegis.router import MockModelProvider, ModelRegistry, ModelRouter
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    Observation,
    TaskState,
)
from aegis.skills import ComputationContext


def _sample_context() -> ComputationContext:
    return ComputationContext(
        user_goal="Calculate average measured thickness per equipment item.",
        file_path="/data/inspection_readings.xlsx",
        sheet_names=["Readings"],
        columns=["Equipment_ID", "Measured_Thickness", "Min_Acceptable_Thickness"],
        numeric_fields=["Measured_Thickness", "Min_Acceptable_Thickness"],
        row_count=3,
        representative_values={"Equipment_ID": ["EQ-001"], "Measured_Thickness": [4.5]},
    )


def _retry_correct_payload() -> dict:
    return {
        "directive": "retry_correct",
        "summary": "Sandbox execution failed; regenerate corrected calculation code.",
        "proposed_action": {
            "action": "generate_code",
            "inputs": {},
            "done": False,
            "summary": "Regenerate code using the sandbox error observation.",
        },
    }


def _continue_run_code_payload() -> dict:
    return {
        "directive": "continue",
        "summary": "Corrected code is ready; execute it in the sandbox.",
        "proposed_action": {
            "action": "run_code",
            "inputs": {},
            "done": False,
            "summary": "Re-run the corrected calculation.",
        },
    }


def _continue_generate_payload() -> dict:
    return {
        "directive": "continue",
        "summary": "Hold; do not request an automatic sandbox correction.",
        "proposed_action": {
            "action": "generate_code",
            "inputs": {},
            "done": False,
            "summary": "Correction remains optional.",
        },
    }


class _MockInspect(Capability):
    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="inspect_spreadsheet",
            kind=CapabilityKind.TOOL,
            description="Mock inspect.",
            input_modalities=("spreadsheet",),
        )

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            observations=[
                Observation(source="inspect_spreadsheet", kind="inspect", summary="schema ready")
            ],
        )


def _registry(generate_cap: GenerateCodeCapability, run_cap: RunCodeCapability) -> CapabilityRegistry:
    config = load_config()
    registry = CapabilityRegistry(config.capabilities)
    registry.register(_MockInspect())
    registry.register(generate_cap)
    registry.register(run_cap)
    return registry


def _stack(
    *,
    sandbox: MockSandboxRunner,
    agent_factory,
    code_response: str = "print(42)",
    max_retries: int = 2,
    max_iterations: int = 12,
):
    config = load_config()
    router = ModelRouter(ModelRegistry(config.models))
    agent_provider = MockModelProvider(response_factory=agent_factory)
    coding_provider = MockModelProvider(response_factory=lambda _req: code_response)
    agent = RouterAgentRuntime(config.agent, router, {"local_ollama": agent_provider})
    generate_cap = GenerateCodeCapability(
        router=router, providers={"local_ollama": coding_provider}
    )
    run_cap = RunCodeCapability(sandbox=sandbox)
    broker = RegistryCapabilityBroker(_registry(generate_cap, run_cap))
    state = TaskState(
        user_goal=_sample_context().user_goal,
        intent="computation",
        modality="spreadsheet",
        max_retries=max_retries,
        max_iterations=max_iterations,
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)
    loop = SandboxObservationLoop(agent, controller)
    return loop, agent_provider, coding_provider, sandbox


def _advance_to_failed_run(controller: ExecutionController, failed_code: str = "print(missing)") -> None:
    controller.execute(
        AgentDecision(action="inspect_spreadsheet", inputs={"workbook": "/data/inspection_readings.xlsx"})
    )
    controller.execute(
        AgentDecision(
            action="generate_code",
            inputs={
                "computation_description": "Average thickness",
                "data_schema": "Equipment_ID, Measured_Thickness",
                "file_path": "/data/inspection_readings.xlsx",
            },
        )
    )
    controller.execute(
        AgentDecision(
            action="run_code",
            inputs={"code": failed_code, "file_path": "/data/inspection_readings.xlsx"},
        )
    )


def test_controller_exposes_sandbox_observation_not_governance_wrapper():
    sandbox = MockSandboxRunner(
        default_result=SandboxResult(stdout="", stderr="KeyError: 'thickness'", exit_code=1)
    )
    loop, _, _, _ = _stack(sandbox=sandbox, agent_factory=lambda _req: json.dumps(_retry_correct_payload()))
    _advance_to_failed_run(loop.controller)

    observation = loop.controller.observation_for_agent()
    assert observation.source == "run_code"
    assert observation.kind == "code_execution"
    assert observation.data["exit_code"] == 1
    assert "KeyError" in str(observation.data.get("stderr", ""))
    assert loop.controller.last_action == "run_code"
    assert loop.controller.last_capability_result is not None
    assert loop.controller.last_capability_result.status == CapabilityResultStatus.FAILED
    assert loop.controller.allowed_next_actions() == ("generate_code",)
    assert loop.controller.state.current_step == "generate"
    assert loop.controller.state.retry_count == 1


def test_agent_receives_structured_sandbox_error_in_reasoning_payload():
    sandbox = MockSandboxRunner(
        default_result=SandboxResult(stdout="", stderr="NameError: name 'x' is not defined", exit_code=1)
    )
    loop, agent_provider, _, _ = _stack(
        sandbox=sandbox, agent_factory=lambda _req: json.dumps(_retry_correct_payload())
    )
    _advance_to_failed_run(loop.controller)

    decision = loop.reason()
    assert decision.directive == AgentDirective.RETRY_CORRECT
    assert decision.proposed_action is not None
    assert decision.proposed_action.action == "generate_code"

    prompt = agent_provider.requests[-1].prompt
    assert "NameError" in prompt
    assert "run_code" in prompt
    assert "generate_code" in prompt
    assert '"status": "failed"' in prompt or "failed" in prompt


def test_act_observe_error_reason_correct_act_success():
    call_count = {"run": 0}

    def sandbox_factory(code, _path):
        call_count["run"] += 1
        if call_count["run"] == 1:
            return SandboxResult(stdout="", stderr="KeyError: 'Min_Acceptable_Thickness'", exit_code=1)
        return SandboxResult(stdout="EQ-001 average=4.5 below=[]", exit_code=0)

    agent_calls = {"n": 0}

    def agent_factory(_req):
        agent_calls["n"] += 1
        if agent_calls["n"] == 1:
            return json.dumps(_retry_correct_payload())
        return json.dumps(_continue_run_code_payload())

    loop, agent_provider, coding_provider, sandbox = _stack(
        sandbox=MockSandboxRunner(result_factory=sandbox_factory),
        agent_factory=agent_factory,
        code_response="print('EQ-001 average=4.5 below=[]')",
    )
    _advance_to_failed_run(loop.controller, failed_code="print(broken)")
    assert loop.controller.execution_events[-1].kind == ExecutionEventKind.ACTION_FAILED

    recovery = loop.recover_from_run_code_failure(
        _sample_context(), "print(broken)"
    )

    assert recovery.error_observation.source == "run_code"
    assert recovery.reason_decision.directive == AgentDirective.RETRY_CORRECT
    assert recovery.correction_event is not None
    assert recovery.correction_event.kind == ExecutionEventKind.ACTION_COMPLETED
    assert recovery.correction_event.action == "generate_code"
    assert recovery.rerun_decision is not None
    assert recovery.rerun_decision.directive == AgentDirective.CONTINUE
    assert recovery.rerun_event is not None
    assert recovery.rerun_event.kind == ExecutionEventKind.ACTION_COMPLETED
    assert recovery.rerun_event.action == "run_code"

    assert sandbox.call_count == 2
    assert sandbox.last_code is not None
    assert "print(" in sandbox.last_code
    assert loop.controller.state.current_step == "verify"
    assert loop.controller.state.final_status == FinalStatus.NOT_FINAL
    assert loop.controller.state.retry_count == 1
    assert agent_calls["n"] == 2
    assert any(
        "Correction Context" in req.prompt or "KeyError" in req.prompt
        for req in coding_provider.requests[1:]
    )
    first_agent_prompt = agent_provider.requests[0].prompt
    assert "KeyError" in first_agent_prompt


def test_controller_retry_limit_blocks_further_correction():
    sandbox = MockSandboxRunner(
        default_result=SandboxResult(stdout="", stderr="always fails", exit_code=1)
    )
    agent_calls = {"n": 0}

    def agent_factory(_req):
        agent_calls["n"] += 1
        if agent_calls["n"] % 2 == 1:
            return json.dumps(_retry_correct_payload())
        return json.dumps(_continue_run_code_payload())

    loop, _, _, _ = _stack(
        sandbox=sandbox,
        agent_factory=agent_factory,
        max_retries=1,
        max_iterations=12,
    )
    _advance_to_failed_run(loop.controller)
    assert loop.controller.state.retry_count == 1
    assert loop.controller.state.final_status == FinalStatus.NOT_FINAL

    recovery = loop.recover_from_run_code_failure(_sample_context(), "print(broken)")

    assert recovery.correction_event is not None
    assert recovery.correction_event.kind == ExecutionEventKind.ACTION_COMPLETED
    assert recovery.rerun_event is not None
    assert recovery.rerun_event.kind == ExecutionEventKind.TASK_FAILED
    assert loop.controller.state.final_status == FinalStatus.FAILED
    assert loop.controller.allowed_next_actions() == ()

    rejected = loop.controller.execute(AgentDecision(action="generate_code", inputs={}))
    assert rejected.kind == ExecutionEventKind.ACTION_REJECTED
    assert sandbox.call_count == 2


def test_agent_may_decline_automatic_correction():
    sandbox = MockSandboxRunner(
        default_result=SandboxResult(stdout="", stderr="ValueError: bad", exit_code=1)
    )
    loop, _, _, sandbox_runner = _stack(
        sandbox=sandbox, agent_factory=lambda _req: json.dumps(_continue_generate_payload())
    )
    _advance_to_failed_run(loop.controller)
    runs_before = sandbox_runner.call_count

    recovery = loop.recover_from_run_code_failure(_sample_context(), "print(broken)")

    assert recovery.reason_decision.directive == AgentDirective.CONTINUE
    assert recovery.correction_event is None
    assert recovery.rerun_event is None
    assert sandbox_runner.call_count == runs_before
    assert loop.controller.state.current_step == "generate"


def test_correction_uses_generated_code_not_agent_invented_payload():
    def sandbox_factory(code, _path):
        if "AUTHORITATIVE_CORRECTED_CODE" in code:
            return SandboxResult(stdout="ok", exit_code=0)
        return SandboxResult(stdout="", stderr="fail", exit_code=1)

    agent_calls = {"n": 0}

    def agent_factory(_req):
        agent_calls["n"] += 1
        if agent_calls["n"] == 1:
            payload = _retry_correct_payload()
            payload["proposed_action"]["inputs"] = {"code": "print('agent-invented')"}
            return json.dumps(payload)
        payload = _continue_run_code_payload()
        payload["proposed_action"]["inputs"] = {"code": "print('agent-invented-rerun')"}
        return json.dumps(payload)

    loop, _, _, sandbox = _stack(
        sandbox=MockSandboxRunner(result_factory=sandbox_factory),
        agent_factory=agent_factory,
        code_response="print('AUTHORITATIVE_CORRECTED_CODE')",
    )
    _advance_to_failed_run(loop.controller)
    loop.recover_from_run_code_failure(_sample_context(), "print(broken)")

    assert sandbox.last_code is not None
    assert "AUTHORITATIVE_CORRECTED_CODE" in sandbox.last_code
    assert "agent-invented" not in sandbox.last_code
