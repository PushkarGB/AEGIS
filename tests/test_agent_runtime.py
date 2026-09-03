"""Tests for the structured Agent Runtime behind Router -> ModelProvider."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aegis.agent import (
    AgentDirective,
    AgentIntent,
    AgentModelResponseError,
    AgentProviderResolutionError,
    AttachmentDescriptor,
    InputModality,
    IntentAnalysisRequest,
    ObservationReasoningRequest,
    PlanGenerationRequest,
    PreviousExecutionContext,
    RouterAgentRuntime,
)
from aegis.config import AgentConfig, AgentPlanningConfig, ModelConfig, ModelProviderConfig, ModelRegistryConfig
from aegis.orchestration import WorkflowName
from aegis.router import MockModelProvider, ModelRegistry, ModelRouter
from aegis.schemas import CapabilityResultStatus, Observation, TaskState, VerificationStatus


def _agent_config() -> AgentConfig:
    return AgentConfig(
        name="aegis-agent",
        description="Structured runtime agent.",
        default_model_role="agent",
        planning=AgentPlanningConfig(
            max_plan_steps=8,
            max_observation_chars=12000,
        ),
        allowed_modalities=[
            "spreadsheet",
            "scanned_document",
            "image",
        ],
        default_capabilities=["finish"],
    )


def _router() -> ModelRouter:
    registry = ModelRegistry(
        ModelRegistryConfig(
            providers=[
                ModelProviderConfig(
                    id="local",
                    kind="local",
                    enabled=True,
                    endpoint="http://localhost:11434/v1",
                )
            ],
            models=[
                ModelConfig(
                    id="agent-model",
                    provider="local",
                    roles=["agent"],
                    capabilities=["reasoning", "planning", "drafting"],
                    task_types=["general_reasoning", "drafting"],
                )
            ],
            role_defaults={"agent": "agent-model"},
        )
    )
    return ModelRouter(registry)


def _runtime(response_payload: dict[str, object]) -> tuple[RouterAgentRuntime, MockModelProvider]:
    provider = MockModelProvider(
        responses={"agent-model": json.dumps(response_payload)}
    )
    runtime = RouterAgentRuntime(
        _agent_config(),
        _router(),
        {"local": provider},
    )
    return runtime, provider


def test_decide_intent_returns_structured_result_via_router_and_provider():
    runtime, provider = _runtime(
        {
            "intent": "computation",
            "modality": "spreadsheet",
            "workflow": "computation",
            "summary": "The goal is a spreadsheet-based calculation task.",
        }
    )

    result = runtime.decide_intent(
        IntentAnalysisRequest(
            user_goal="Calculate average thickness by equipment item.",
            attachments=[
                AttachmentDescriptor(
                    name="readings.xlsx",
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            ],
        )
    )

    assert result.intent == AgentIntent.COMPUTATION
    assert result.modality == InputModality.SPREADSHEET
    assert result.workflow == WorkflowName.COMPUTATION
    assert len(provider.requests) == 1
    assert provider.requests[0].model_id == "agent-model"
    assert "readings.xlsx" in provider.requests[0].prompt


def test_generate_plan_returns_bounded_controller_ready_steps():
    runtime, provider = _runtime(
        {
            "intent": "computation",
            "modality": "spreadsheet",
            "workflow": "computation",
            "summary": "Inspect the workbook, compute the result, verify it, and deliver it.",
            "steps": [
                {
                    "capability_name": "inspect_spreadsheet",
                    "purpose": "Inspect workbook structure and identify required columns.",
                    "inputs": {},
                },
                {
                    "capability_name": "generate_code",
                    "purpose": "Generate deterministic calculation code for the inspected schema.",
                    "inputs": {"goal": "average thickness by equipment"},
                },
                {
                    "capability_name": "run_code",
                    "purpose": "Execute generated code in the sandbox.",
                    "inputs": {},
                },
                {
                    "capability_name": "verify_result",
                    "purpose": "Verify the computed output against deterministic checks.",
                    "inputs": {},
                },
                {
                    "capability_name": "generate_excel",
                    "purpose": "Prepare a spreadsheet deliverable for the user.",
                    "inputs": {},
                },
                {
                    "capability_name": "finish",
                    "purpose": "Mark the task ready to finish after validation.",
                    "inputs": {},
                },
            ],
        }
    )

    result = runtime.generate_plan(
        PlanGenerationRequest(
            user_goal="Calculate average thickness by equipment item.",
            intent=AgentIntent.COMPUTATION,
            modality=InputModality.SPREADSHEET,
            available_capabilities=[
                "inspect_spreadsheet",
                "generate_code",
                "run_code",
                "verify_result",
                "generate_excel",
                "finish",
            ],
        )
    )

    assert [step.capability_name for step in result.steps] == [
        "inspect_spreadsheet",
        "generate_code",
        "run_code",
        "verify_result",
        "generate_excel",
        "finish",
    ]
    assert len(result.steps) <= 8
    assert len(provider.requests) == 1
    assert "available_capabilities" in provider.requests[0].prompt


def test_generate_plan_rejects_unavailable_or_missing_required_capabilities():
    runtime, _ = _runtime(
        {
            "intent": "computation",
            "modality": "spreadsheet",
            "workflow": "computation",
            "summary": "Skip directly to execution.",
            "steps": [
                {
                    "capability_name": "run_code",
                    "purpose": "Run code immediately.",
                    "inputs": {},
                },
                {
                    "capability_name": "finish",
                    "purpose": "Finish the task.",
                    "inputs": {},
                },
            ],
        }
    )

    with pytest.raises(AgentModelResponseError, match="omitted required capabilities"):
        runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Calculate average thickness by equipment item.",
                intent=AgentIntent.COMPUTATION,
                modality=InputModality.SPREADSHEET,
                available_capabilities=[
                    "inspect_spreadsheet",
                    "generate_code",
                    "run_code",
                    "verify_result",
                    "generate_excel",
                    "finish",
                ],
            )
        )


def test_reason_about_observation_returns_retry_correct_with_structured_action():
    runtime, provider = _runtime(
        {
            "directive": "retry_correct",
            "summary": "The sandbox error requires corrected code generation before continuing.",
            "proposed_action": {
                "action": "generate_code",
                "inputs": {"correction": "handle missing minimum thickness column"},
                "done": False,
                "summary": "Regenerate the calculation with corrected column handling.",
            },
        }
    )
    observation = Observation(
        source="sandbox",
        kind="execution_error",
        summary="Generated code failed because the minimum thickness column was missing.",
        data={"stderr": "KeyError: minimum_thickness"},
    )

    result = runtime.reason_about_observation(
        ObservationReasoningRequest(
            task_state=TaskState(
                session_id=uuid4(),
                user_goal="Calculate average equipment thickness.",
                intent="computation",
                modality="spreadsheet",
                current_step="generate",
            ),
            latest_observation=observation,
            previous_context=PreviousExecutionContext(
                last_action="run_code",
                last_result_status=CapabilityResultStatus.FAILED,
                allowed_next_actions=["generate_code"],
                controller_summary="Sandbox execution failed; correction is allowed.",
            ),
        )
    )

    assert result.directive == AgentDirective.RETRY_CORRECT
    assert result.proposed_action is not None
    assert result.proposed_action.action == "generate_code"
    assert provider.requests[0].model_id == "agent-model"


def test_reason_about_observation_supports_request_approval_without_action():
    runtime, _ = _runtime(
        {
            "directive": "request_approval",
            "summary": "Verification passed and the workflow is waiting for human approval.",
        }
    )
    observation = Observation(
        source="execution_controller",
        kind="capability_succeeded",
        summary="Verification capability completed successfully.",
    )

    result = runtime.reason_about_observation(
        ObservationReasoningRequest(
            task_state=TaskState(
                user_goal="Prepare an approval note.",
                intent="document_drafting",
                modality="scanned_document",
                current_step="finish",
                verification_status=VerificationStatus.PASSED,
            ),
            latest_observation=observation,
            previous_context=PreviousExecutionContext(
                last_action="verify_result",
                last_result_status=CapabilityResultStatus.SUCCEEDED,
                allowed_next_actions=[],
                controller_summary="Verification passed; workflow requires human approval before finish.",
            ),
        )
    )

    assert result.directive == AgentDirective.REQUEST_APPROVAL
    assert result.proposed_action is None


def test_runtime_rejects_missing_router_selected_provider():
    runtime = RouterAgentRuntime(
        _agent_config(),
        _router(),
        {},
    )

    with pytest.raises(AgentProviderResolutionError, match="No ModelProvider instance"):
        runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Calculate average thickness by equipment item.",
                attachments=[],
            )
        )
