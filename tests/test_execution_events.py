"""Tests for the provider-neutral execution event contract."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from aegis.agent import AttachmentDescriptor, IntentAnalysisRequest, RouterAgentRuntime
from aegis.config import AgentConfig, AgentPlanningConfig, ModelConfig, ModelProviderConfig, ModelRegistryConfig
from aegis.events import (
    ExecutionEvent,
    ExecutionEventContext,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration import ExecutionController, WorkflowName
from aegis.router import MockModelProvider, ModelRegistry, ModelRouter
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    TaskState,
)
from aegis.broker import CapabilityBroker


class _SuccessfulBroker(CapabilityBroker):
    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"exit_code": 0, "timed_out": False} if request.capability_name == "run_code" else {},
        )


def test_controller_events_are_streamable_auditable_and_contextual():
    state = TaskState(user_goal="Calculate equipment thickness averages.", user_id="operator-42")
    publisher = ExecutionEventPublisher()
    streamed: list[ExecutionEvent] = []
    publisher.subscribe(streamed.append)
    controller = ExecutionController(
        state, WorkflowName.COMPUTATION, _SuccessfulBroker(), publisher
    )

    for action in (
        "inspect_spreadsheet",
        "generate_code",
        "run_code",
        "verify_result",
        "generate_excel",
    ):
        controller.execute(AgentDecision(action=action))
    controller.execute(AgentDecision(action="finish", done=True))

    event_types = [event.event_type for event in controller.execution_events]
    assert event_types[:2] == [
        ExecutionEventType.TASK_STARTED,
        ExecutionEventType.WORKFLOW_SELECTED,
    ]
    assert ExecutionEventType.CAPABILITY_STARTED in event_types
    assert ExecutionEventType.CAPABILITY_COMPLETED in event_types
    assert ExecutionEventType.SANDBOX_STARTED in event_types
    assert ExecutionEventType.SANDBOX_COMPLETED in event_types
    assert ExecutionEventType.VERIFICATION_STARTED in event_types
    assert ExecutionEventType.VERIFICATION_COMPLETED in event_types
    assert event_types[-1] == ExecutionEventType.TASK_COMPLETED
    assert streamed == list(controller.execution_events)
    assert [event.sequence for event in streamed] == list(range(1, len(streamed) + 1))
    assert all(event.session_id == state.session_id for event in streamed)
    assert all(event.task_id == state.task_id for event in streamed)
    assert all(event.user_id == "operator-42" for event in streamed)
    sandbox_event = next(
        event for event in streamed if event.event_type == ExecutionEventType.SANDBOX_COMPLETED
    )
    assert sandbox_event.metadata == {"exit_code": 0, "timed_out": False}
    assert json.loads(streamed[-1].model_dump_json())["event_type"] == "task_completed"


def test_agent_runtime_emits_model_and_intent_events_with_mock_provider():
    registry = ModelRegistry(
        ModelRegistryConfig(
            providers=[ModelProviderConfig(id="mock", kind="mock")],
            models=[
                ModelConfig(
                    id="agent-model",
                    provider="mock",
                    roles=["agent"],
                    capabilities=["reasoning"],
                    task_types=["general_reasoning"],
                )
            ],
            role_defaults={"agent": "agent-model"},
        )
    )
    provider = MockModelProvider(
        responses={
            "agent-model": json.dumps(
                {
                    "intent": "computation",
                    "modality": "spreadsheet",
                    "workflow": "computation",
                    "summary": "Spreadsheet computation identified.",
                }
            )
        }
    )
    publisher = ExecutionEventPublisher()
    runtime = RouterAgentRuntime(
        AgentConfig(
            name="aegis-agent",
            description="Structured runtime agent.",
            default_model_role="agent",
            planning=AgentPlanningConfig(max_plan_steps=8, max_observation_chars=256),
            allowed_modalities=["spreadsheet"],
        ),
        ModelRouter(registry),
        {"mock": provider},
        publisher,
    )
    state = TaskState(user_goal="Calculate equipment thickness averages.")

    runtime.decide_intent(
        IntentAnalysisRequest(
            user_goal=state.user_goal,
            attachments=[AttachmentDescriptor(name="readings.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            event_context=ExecutionEventContext(
                session_id=state.session_id,
                task_id=state.task_id,
            ),
        )
    )

    assert [event.event_type for event in publisher.events] == [
        ExecutionEventType.MODEL_SELECTED,
        ExecutionEventType.MODEL_INVOKED,
        ExecutionEventType.INTENT_IDENTIFIED,
    ]
    selected, invoked, intent = publisher.events
    assert selected.model_id == invoked.model_id == "agent-model"
    assert selected.model_provider_id == invoked.model_provider_id == "mock"
    assert invoked.status == ExecutionEventStatus.COMPLETED
    assert intent.metadata == {"intent": "computation", "modality": "spreadsheet"}


def test_execution_event_rejects_unknown_fields_and_naive_timestamps():
    state = TaskState(user_goal="Test event validation.")
    base = {
        "session_id": state.session_id,
        "task_id": state.task_id,
        "event_type": "task_started",
        "component": "execution_controller",
        "status": "started",
        "summary": "Task execution started.",
    }

    with pytest.raises(ValidationError):
        ExecutionEvent(**base, timestamp="2026-09-03T09:00:00")
    with pytest.raises(ValidationError):
        ExecutionEvent(**base, unexpected="not permitted")
