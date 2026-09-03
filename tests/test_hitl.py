"""Comprehensive state-transition tests for the HITL approval state machine.

Test classes
------------
TestHITLApprovalStateEnum          — enum values, membership, string repr
TestHITLApprovalDecision           — record fields, UTC tz, immutability, JSON
TestHITLStateMachineInitial        — initial state and empty history
TestHITLStateMachineValidTransitions   — all 4 valid transitions succeed
TestHITLStateMachineInvalidTransitions — all illegal transitions raise error
TestHITLStateMachineHistory        — history ordering, accumulation, immutability
TestControllerHITLIntegration      — controller-level HITL behaviour end-to-end
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from aegis.broker import CapabilityBroker
from aegis.orchestration import (
    ExecutionController,
    ExecutionEventKind,
    HITLApprovalDecision,
    HITLApprovalState,
    HITLApprovalStateMachine,
    InvalidHITLTransitionError,
    WorkflowName,
)
from aegis.schemas import (
    AgentDecision,
    ApprovalStatus,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    TaskState,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_sm(user_id: str | None = None) -> HITLApprovalStateMachine:
    return HITLApprovalStateMachine(
        task_id=uuid4(),
        session_id=uuid4(),
        user_id=user_id,
    )


class _MockBroker(CapabilityBroker):
    def __init__(self, responder: Callable[[CapabilityRequest], CapabilityResult]) -> None:
        self._responder = responder

    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        return self._responder(request)


def _succeeds(request: CapabilityRequest) -> CapabilityResult:
    return CapabilityResult(request_id=request.request_id, status=CapabilityResultStatus.SUCCEEDED)


def _fails(request: CapabilityRequest) -> CapabilityResult:
    return CapabilityResult(
        request_id=request.request_id,
        status=CapabilityResultStatus.FAILED,
        error="Synthetic failure.",
    )


def _approval_workflow_controller(user_id: str | None = None) -> ExecutionController:
    state = TaskState(user_goal="Prepare an approval note.", user_id=user_id)
    return ExecutionController(state, WorkflowName.SCANNED_DOCUMENT_APPROVAL, _MockBroker(_succeeds))


def _advance_to_verify(controller: ExecutionController) -> None:
    """Run the approval workflow up to (and including) verify_result."""
    for action in ("extract_document", "ocr_document", "draft_approval_note", "generate_word"):
        controller.execute(AgentDecision(action=action))
    controller.execute(AgentDecision(action="verify_result"))


# ===========================================================================
# 1. Enum
# ===========================================================================

class TestHITLApprovalStateEnum:
    def test_all_required_states_exist(self):
        required = {"draft", "waiting_for_approval", "approved", "rejected", "final"}
        actual = {s.value for s in HITLApprovalState}
        assert required == actual

    def test_states_are_str_instances(self):
        for state in HITLApprovalState:
            assert isinstance(state, str)

    def test_str_repr_matches_value(self):
        assert str(HITLApprovalState.DRAFT) == "draft"
        assert str(HITLApprovalState.WAITING_FOR_APPROVAL) == "waiting_for_approval"
        assert str(HITLApprovalState.APPROVED) == "approved"
        assert str(HITLApprovalState.REJECTED) == "rejected"
        assert str(HITLApprovalState.FINAL) == "final"

    def test_membership_by_value(self):
        assert HITLApprovalState("draft") is HITLApprovalState.DRAFT
        assert HITLApprovalState("final") is HITLApprovalState.FINAL

    def test_count(self):
        assert len(HITLApprovalState) == 5


# ===========================================================================
# 2. Decision record
# ===========================================================================

class TestHITLApprovalDecision:
    def _make(self, **kwargs) -> HITLApprovalDecision:
        task_id = uuid4()
        session_id = uuid4()
        defaults = dict(
            task_id=task_id,
            session_id=session_id,
            previous_state=HITLApprovalState.DRAFT,
            new_state=HITLApprovalState.WAITING_FOR_APPROVAL,
            decision="submit",
        )
        defaults.update(kwargs)
        return HITLApprovalDecision(**defaults)

    def test_required_fields_present(self):
        d = self._make()
        assert isinstance(d.decision_id, UUID)
        assert isinstance(d.task_id, UUID)
        assert isinstance(d.session_id, UUID)
        assert isinstance(d.timestamp, datetime)
        assert isinstance(d.previous_state, HITLApprovalState)
        assert isinstance(d.new_state, HITLApprovalState)
        assert isinstance(d.decision, str)

    def test_user_id_optional(self):
        d = self._make()
        assert d.user_id is None

    def test_user_id_stored(self):
        d = self._make(user_id="operator-1")
        assert d.user_id == "operator-1"

    def test_timestamp_is_utc(self):
        d = self._make()
        assert d.timestamp.tzinfo is not None

    def test_naive_timestamp_raises(self):
        with pytest.raises(Exception):
            self._make(timestamp=datetime(2026, 1, 1))  # naive

    def test_frozen_immutable(self):
        d = self._make()
        with pytest.raises(Exception):
            d.decision = "mutated"  # type: ignore[misc]

    def test_json_serializable(self):
        d = self._make(user_id="op-1")
        payload = d.model_dump(mode="json")
        # Round-trip through JSON
        raw = json.dumps(payload)
        loaded = json.loads(raw)
        assert loaded["decision"] == "submit"
        assert loaded["previous_state"] == "draft"
        assert loaded["new_state"] == "waiting_for_approval"
        assert loaded["user_id"] == "op-1"

    def test_decision_id_is_unique_per_instance(self):
        d1 = self._make()
        d2 = self._make()
        assert d1.decision_id != d2.decision_id


# ===========================================================================
# 3. Initial state
# ===========================================================================

class TestHITLStateMachineInitial:
    def test_initial_state_is_draft(self):
        sm = _make_sm()
        assert sm.state == HITLApprovalState.DRAFT

    def test_initial_history_empty(self):
        sm = _make_sm()
        assert sm.history == ()

    def test_task_and_session_stored(self):
        task_id = uuid4()
        session_id = uuid4()
        sm = HITLApprovalStateMachine(task_id=task_id, session_id=session_id)
        assert sm._task_id == task_id
        assert sm._session_id == session_id


# ===========================================================================
# 4. Valid transitions
# ===========================================================================

class TestHITLStateMachineValidTransitions:
    def test_draft_to_waiting_for_approval(self):
        sm = _make_sm()
        decision = sm.submit()
        assert sm.state == HITLApprovalState.WAITING_FOR_APPROVAL
        assert decision.previous_state == HITLApprovalState.DRAFT
        assert decision.new_state == HITLApprovalState.WAITING_FOR_APPROVAL
        assert decision.decision == "submit"

    def test_waiting_for_approval_to_approved(self):
        sm = _make_sm()
        sm.submit()
        decision = sm.approve()
        assert sm.state == HITLApprovalState.APPROVED
        assert decision.previous_state == HITLApprovalState.WAITING_FOR_APPROVAL
        assert decision.new_state == HITLApprovalState.APPROVED
        assert decision.decision == "approve"

    def test_waiting_for_approval_to_rejected(self):
        sm = _make_sm()
        sm.submit()
        decision = sm.reject()
        assert sm.state == HITLApprovalState.REJECTED
        assert decision.previous_state == HITLApprovalState.WAITING_FOR_APPROVAL
        assert decision.new_state == HITLApprovalState.REJECTED
        assert decision.decision == "reject"

    def test_approved_to_final(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        decision = sm.finalize()
        assert sm.state == HITLApprovalState.FINAL
        assert decision.previous_state == HITLApprovalState.APPROVED
        assert decision.new_state == HITLApprovalState.FINAL
        assert decision.decision == "finalize"

    def test_decision_carries_user_id_from_method(self):
        sm = _make_sm()
        d = sm.submit(user_id="operator-99")
        assert d.user_id == "operator-99"

    def test_decision_falls_back_to_default_user_id(self):
        sm = _make_sm(user_id="default-user")
        d = sm.submit()
        assert d.user_id == "default-user"

    def test_per_call_user_id_overrides_default(self):
        sm = _make_sm(user_id="default-user")
        d = sm.submit(user_id="per-call-user")
        assert d.user_id == "per-call-user"

    def test_decision_carries_task_and_session_ids(self):
        task_id = uuid4()
        session_id = uuid4()
        sm = HITLApprovalStateMachine(task_id=task_id, session_id=session_id)
        d = sm.submit()
        assert d.task_id == task_id
        assert d.session_id == session_id

    def test_decision_timestamp_is_utc_aware(self):
        sm = _make_sm()
        d = sm.submit()
        assert d.timestamp.tzinfo is not None


# ===========================================================================
# 5. Invalid transitions
# ===========================================================================

class TestHITLStateMachineInvalidTransitions:
    def test_draft_cannot_approve(self):
        sm = _make_sm()
        with pytest.raises(InvalidHITLTransitionError) as exc_info:
            sm.approve()
        assert exc_info.value.from_state == HITLApprovalState.DRAFT
        assert exc_info.value.to_state == HITLApprovalState.APPROVED

    def test_draft_cannot_reject(self):
        sm = _make_sm()
        with pytest.raises(InvalidHITLTransitionError):
            sm.reject()

    def test_draft_cannot_finalize(self):
        sm = _make_sm()
        with pytest.raises(InvalidHITLTransitionError):
            sm.finalize()

    def test_waiting_cannot_submit_again(self):
        sm = _make_sm()
        sm.submit()
        with pytest.raises(InvalidHITLTransitionError):
            sm.submit()

    def test_waiting_cannot_finalize(self):
        sm = _make_sm()
        sm.submit()
        with pytest.raises(InvalidHITLTransitionError):
            sm.finalize()

    def test_approved_cannot_submit(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        with pytest.raises(InvalidHITLTransitionError):
            sm.submit()

    def test_approved_cannot_reject(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        with pytest.raises(InvalidHITLTransitionError):
            sm.reject()

    def test_approved_cannot_approve_again(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        with pytest.raises(InvalidHITLTransitionError):
            sm.approve()

    def test_rejected_is_terminal_cannot_submit(self):
        sm = _make_sm()
        sm.submit()
        sm.reject()
        with pytest.raises(InvalidHITLTransitionError):
            sm.submit()

    def test_rejected_is_terminal_cannot_approve(self):
        sm = _make_sm()
        sm.submit()
        sm.reject()
        with pytest.raises(InvalidHITLTransitionError):
            sm.approve()

    def test_final_is_terminal_cannot_submit(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        sm.finalize()
        with pytest.raises(InvalidHITLTransitionError):
            sm.submit()

    def test_final_is_terminal_cannot_finalize_again(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        sm.finalize()
        with pytest.raises(InvalidHITLTransitionError):
            sm.finalize()

    def test_error_is_value_error_subclass(self):
        sm = _make_sm()
        with pytest.raises(ValueError):
            sm.approve()

    def test_error_message_contains_states(self):
        sm = _make_sm()
        with pytest.raises(InvalidHITLTransitionError) as exc_info:
            sm.approve()
        msg = str(exc_info.value)
        assert "draft" in msg
        assert "approved" in msg

    def test_state_unchanged_after_invalid_transition(self):
        sm = _make_sm()
        try:
            sm.approve()
        except InvalidHITLTransitionError:
            pass
        assert sm.state == HITLApprovalState.DRAFT

    def test_history_unchanged_after_invalid_transition(self):
        sm = _make_sm()
        try:
            sm.approve()
        except InvalidHITLTransitionError:
            pass
        assert sm.history == ()


# ===========================================================================
# 6. History
# ===========================================================================

class TestHITLStateMachineHistory:
    def test_history_accumulates_in_order(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        sm.finalize()
        h = sm.history
        assert len(h) == 3
        assert h[0].decision == "submit"
        assert h[1].decision == "approve"
        assert h[2].decision == "finalize"

    def test_history_is_tuple_of_decisions(self):
        sm = _make_sm()
        sm.submit()
        h = sm.history
        assert isinstance(h, tuple)
        assert all(isinstance(d, HITLApprovalDecision) for d in h)

    def test_history_returns_copy_not_live_list(self):
        sm = _make_sm()
        h1 = sm.history
        sm.submit()
        h2 = sm.history
        assert len(h1) == 0
        assert len(h2) == 1

    def test_rejected_path_history(self):
        sm = _make_sm()
        sm.submit()
        sm.reject()
        h = sm.history
        assert len(h) == 2
        assert h[0].new_state == HITLApprovalState.WAITING_FOR_APPROVAL
        assert h[1].new_state == HITLApprovalState.REJECTED

    def test_each_decision_has_unique_id(self):
        sm = _make_sm()
        sm.submit()
        sm.approve()
        sm.finalize()
        ids = [d.decision_id for d in sm.history]
        assert len(ids) == len(set(ids))

    def test_decision_records_are_frozen(self):
        sm = _make_sm()
        sm.submit()
        decision = sm.history[0]
        with pytest.raises(Exception):
            decision.decision = "mutated"  # type: ignore[misc]


# ===========================================================================
# 7. Controller integration
# ===========================================================================

class TestControllerHITLIntegration:
    def test_hitl_state_none_for_non_approval_workflow(self):
        state = TaskState(user_goal="Run a calculation.")
        controller = ExecutionController(state, WorkflowName.COMPUTATION, _MockBroker(_succeeds))
        assert controller.hitl_state is None
        assert controller.hitl_history == ()

    def test_hitl_state_starts_at_draft_for_approval_workflow(self):
        controller = _approval_workflow_controller()
        assert controller.hitl_state == HITLApprovalState.DRAFT

    def test_hitl_history_empty_before_verify(self):
        controller = _approval_workflow_controller()
        assert controller.hitl_history == ()

    def test_hitl_state_becomes_waiting_after_verify_result(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        assert controller.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL

    def test_hitl_history_has_submit_after_verify_result(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        h = controller.hitl_history
        assert len(h) == 1
        assert h[0].decision == "submit"
        assert h[0].previous_state == HITLApprovalState.DRAFT
        assert h[0].new_state == HITLApprovalState.WAITING_FOR_APPROVAL

    def test_record_approval_true_sets_hitl_approved(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        assert controller.hitl_state == HITLApprovalState.APPROVED

    def test_record_approval_false_sets_hitl_rejected(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)
        assert controller.hitl_state == HITLApprovalState.REJECTED

    def test_finish_after_approval_sets_hitl_final(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        controller.execute(AgentDecision(action="finish", done=True))
        assert controller.hitl_state == HITLApprovalState.FINAL

    def test_full_approval_path_history_length(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        controller.execute(AgentDecision(action="finish", done=True))
        # submit → approve → finalize
        assert len(controller.hitl_history) == 3

    def test_full_approval_path_history_decisions(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        controller.execute(AgentDecision(action="finish", done=True))
        decisions = [d.decision for d in controller.hitl_history]
        assert decisions == ["submit", "approve", "finalize"]

    def test_rejection_path_history_length(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)
        assert len(controller.hitl_history) == 2

    def test_rejection_path_history_decisions(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)
        decisions = [d.decision for d in controller.hitl_history]
        assert decisions == ["submit", "reject"]

    def test_record_approval_for_non_approval_workflow_rejected(self):
        state = TaskState(user_goal="Run a calculation.")
        controller = ExecutionController(state, WorkflowName.COMPUTATION, _MockBroker(_succeeds))
        event = controller.record_approval(True)
        assert event.kind == ExecutionEventKind.ACTION_REJECTED

    def test_record_approval_on_terminal_task_rejected(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)  # cancels task
        event = controller.record_approval(True)
        assert event.kind == ExecutionEventKind.ACTION_REJECTED

    def test_record_approval_before_verify_result_rejected(self):
        controller = _approval_workflow_controller()
        # Only advance partway — no verify_result yet.
        for action in ("extract_document", "ocr_document", "draft_approval_note", "generate_word"):
            controller.execute(AgentDecision(action=action))
        event = controller.record_approval(True)
        assert event.kind == ExecutionEventKind.ACTION_REJECTED

    def test_record_approval_returns_approval_recorded_event(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        event = controller.record_approval(True)
        assert event.kind == ExecutionEventKind.APPROVAL_RECORDED

    def test_approval_event_metadata_contains_hitl_decision(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        event = controller.record_approval(True)
        assert "hitl_decision" in event.metadata
        assert event.metadata["hitl_decision"] == "approve"
        assert "hitl_previous_state" in event.metadata
        assert "hitl_new_state" in event.metadata
        assert "hitl_decision_id" in event.metadata
        assert "hitl_timestamp" in event.metadata

    def test_rejection_event_metadata_contains_hitl_decision(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        event = controller.record_approval(False)
        assert event.metadata["hitl_decision"] == "reject"

    def test_finish_event_metadata_contains_finalize_decision(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        event = controller.execute(AgentDecision(action="finish", done=True))
        assert "hitl_decision" in event.metadata
        assert event.metadata["hitl_decision"] == "finalize"

    def test_record_approval_user_id_propagated_to_decision(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True, user_id="senior-engineer")
        approve_decision = controller.hitl_history[1]
        assert approve_decision.user_id == "senior-engineer"

    def test_record_approval_without_user_id_falls_back_to_task_user(self):
        controller = _approval_workflow_controller(user_id="task-owner")
        _advance_to_verify(controller)
        controller.record_approval(True)
        approve_decision = controller.hitl_history[1]
        assert approve_decision.user_id == "task-owner"

    def test_decision_records_carry_correct_task_and_session_ids(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        for d in controller.hitl_history:
            assert d.task_id == controller.state.task_id
            assert d.session_id == controller.state.session_id

    def test_final_task_status_after_full_approval(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        controller.execute(AgentDecision(action="finish", done=True))
        assert controller.state.final_status == FinalStatus.COMPLETED

    def test_task_approval_status_after_approval(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(True)
        assert controller.state.approval_status == ApprovalStatus.APPROVED

    def test_task_approval_status_after_rejection(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)
        assert controller.state.approval_status == ApprovalStatus.REJECTED

    def test_task_final_status_cancelled_after_rejection(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        controller.record_approval(False)
        assert controller.state.final_status == FinalStatus.CANCELLED

    def test_finish_without_approval_rejected(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        event = controller.execute(AgentDecision(action="finish", done=True))
        assert event.kind == ExecutionEventKind.ACTION_REJECTED

    def test_hitl_required_event_emitted_after_verify(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        event_types = [e.kind for e in controller.execution_events]
        from aegis.events import ExecutionEventType
        assert ExecutionEventType.HITL_REQUIRED in event_types

    def test_decision_record_task_id_matches_state_task_id(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        submit_decision = controller.hitl_history[0]
        assert submit_decision.task_id == controller.state.task_id

    def test_decision_record_session_id_matches_state_session_id(self):
        controller = _approval_workflow_controller()
        _advance_to_verify(controller)
        submit_decision = controller.hitl_history[0]
        assert submit_decision.session_id == controller.state.session_id
