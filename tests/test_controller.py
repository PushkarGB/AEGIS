"""Tests for deterministic Controller governance using a mock Broker."""

from __future__ import annotations

from collections.abc import Callable

from aegis.broker import CapabilityBroker
from aegis.orchestration import ExecutionController, ExecutionEventKind, WorkflowName
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    TaskState,
    VerificationStatus,
)


class MockCapabilityBroker(CapabilityBroker):
    """In-memory Broker that returns deterministic test-only results."""

    def __init__(self, responder: Callable[[CapabilityRequest], CapabilityResult]) -> None:
        self.responder = responder
        self.requests: list[CapabilityRequest] = []

    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        self.requests.append(request)
        return self.responder(request)


def _succeeds(request: CapabilityRequest) -> CapabilityResult:
    return CapabilityResult(
        request_id=request.request_id,
        status=CapabilityResultStatus.SUCCEEDED,
    )


def _fails(request: CapabilityRequest) -> CapabilityResult:
    return CapabilityResult(
        request_id=request.request_id,
        status=CapabilityResultStatus.FAILED,
        error="Synthetic capability failure.",
    )


def test_controller_completes_computation_through_broker_boundary():
    broker = MockCapabilityBroker(_succeeds)
    state = TaskState(user_goal="Calculate average equipment thickness.")
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    for action in (
        "inspect_spreadsheet",
        "generate_code",
        "run_code",
        "verify_result",
        "generate_excel",
    ):
        controller.execute(AgentDecision(action=action))
    event = controller.execute(AgentDecision(action="finish", done=True))

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
    assert state.iteration_count == 6
    assert len(state.observations) == 6
    assert [request.capability_name for request in broker.requests] == state.completed_steps
    assert all(request.task_id == state.session_id for request in broker.requests)
    assert event.kind == ExecutionEventKind.TASK_COMPLETED
    assert controller.execution_events[-1].kind == ExecutionEventKind.TASK_COMPLETED


def test_controller_rejects_invalid_actions_without_invoking_broker():
    broker = MockCapabilityBroker(_succeeds)
    state = TaskState(user_goal="Calculate average equipment thickness.")
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    event = controller.execute(AgentDecision(action="run_code"))

    assert event.kind == ExecutionEventKind.ACTION_REJECTED
    assert broker.requests == []
    assert state.iteration_count == 0
    assert state.current_step == "inspect"
    assert state.final_status == FinalStatus.NOT_FINAL


def test_controller_stops_after_bounded_capability_failures():
    broker = MockCapabilityBroker(_fails)
    state = TaskState(
        user_goal="Inspect a spreadsheet.",
        max_retries=1,
        max_iterations=5,
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    first_event = controller.execute(AgentDecision(action="inspect_spreadsheet"))
    final_event = controller.execute(AgentDecision(action="inspect_spreadsheet"))

    assert first_event.kind == ExecutionEventKind.ACTION_FAILED
    assert state.retry_count == 1
    assert final_event.kind == ExecutionEventKind.TASK_FAILED
    assert state.final_status == FinalStatus.FAILED
    assert len(broker.requests) == 2
    assert len(state.observations) == 2


def test_controller_stops_before_exceeding_iteration_limit():
    broker = MockCapabilityBroker(_succeeds)
    state = TaskState(
        user_goal="Inspect a spreadsheet.",
        max_iterations=1,
    )
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    controller.execute(AgentDecision(action="inspect_spreadsheet"))
    event = controller.execute(AgentDecision(action="generate_code"))

    assert event.kind == ExecutionEventKind.LIMIT_EXCEEDED
    assert state.final_status == FinalStatus.FAILED
    assert len(broker.requests) == 1


def test_controller_requires_approval_before_finishing_approval_note():
    broker = MockCapabilityBroker(_succeeds)
    state = TaskState(user_goal="Prepare an approval note.")
    controller = ExecutionController(
        state, WorkflowName.SCANNED_DOCUMENT_APPROVAL, broker
    )

    for action in (
        "extract_document",
        "ocr_document",
        "draft_approval_note",
        "generate_word",
        "verify_result",
    ):
        controller.execute(AgentDecision(action=action))
    rejected = controller.execute(AgentDecision(action="finish", done=True))
    approved = controller.record_approval(True)
    completed = controller.execute(AgentDecision(action="finish", done=True))

    assert rejected.kind == ExecutionEventKind.ACTION_REJECTED
    assert approved.kind == ExecutionEventKind.APPROVAL_RECORDED
    assert completed.kind == ExecutionEventKind.TASK_COMPLETED
    assert state.final_status == FinalStatus.COMPLETED
