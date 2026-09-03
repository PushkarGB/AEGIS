"""Tests for HITL approval state transitions and enforcement.

Verifies:
1. Approve once: state becomes APPROVED then FINAL on finish; final_status COMPLETED.
2. Reject once: state becomes REJECTED; final_status CANCELLED; UI says "Rejected" without "cancelled".
3. Approve twice: second attempt is rejected by backend Controller.
4. Reject twice: second attempt is rejected by backend Controller.
5. Approve after reject: second attempt is rejected by backend Controller.
6. Reject after approve: second attempt is rejected by backend Controller.
7. UI reflects final state (approval controls become hidden/disabled; final state display).
"""

from __future__ import annotations

from uuid import UUID, uuid4
import pytest

from aegis.broker import CapabilityBroker
from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration import (
    ExecutionController,
    HITLApprovalDecision,
    HITLApprovalState,
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
)
from aegis.sessions.models import TaskStatus
from aegis.ui.app import handle_approval_decision
from aegis.ui.runner import DeterministicTaskRunner, ExecutionRunResult
from aegis.ui.service import UIBackendService, UITaskResult


def _succeeds(request: CapabilityRequest) -> CapabilityResult:
    return CapabilityResult(request_id=request.request_id, status=CapabilityResultStatus.SUCCEEDED)


class _MockBroker(CapabilityBroker):
    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        return _succeeds(request)


def _make_waiting_controller(user_id: str = "alice") -> ExecutionController:
    """Create an ExecutionController advanced to WAITING_FOR_APPROVAL."""
    state = TaskState(
        task_id=uuid4(),
        session_id=uuid4(),
        user_id=user_id,
        user_goal="Review scanned inspection report and prepare approval note.",
        attachments=["report.pdf"],
    )
    ctrl = ExecutionController(
        state=state,
        workflow=WorkflowName.SCANNED_DOCUMENT_APPROVAL,
        broker=_MockBroker(),
    )
    for action in ("extract_document", "ocr_document", "draft_approval_note", "generate_word", "verify_result"):
        ctrl.execute(AgentDecision(action=action))
    assert ctrl.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL
    return ctrl


# ===========================================================================
# 1. Controller-Level Transition Tests
# ===========================================================================

class TestControllerHITLTransitions:
    """Proves Controller-level HITL transition enforcement."""

    def test_approve_once(self):
        ctrl = _make_waiting_controller()
        event = ctrl.record_approval(True, user_id="alice")
        assert event.status == ExecutionEventStatus.COMPLETED
        assert ctrl.hitl_state == HITLApprovalState.APPROVED
        assert ctrl.state.approval_status == ApprovalStatus.APPROVED

        # Finish transitions to FINAL
        finish_event = ctrl.execute(AgentDecision(action="finish", done=True))
        assert finish_event.status == ExecutionEventStatus.COMPLETED
        assert ctrl.hitl_state == HITLApprovalState.FINAL
        assert ctrl.state.final_status == FinalStatus.COMPLETED

    def test_reject_once(self):
        ctrl = _make_waiting_controller()
        event = ctrl.record_approval(False, user_id="alice")
        assert event.status == ExecutionEventStatus.COMPLETED
        assert ctrl.hitl_state == HITLApprovalState.REJECTED
        assert ctrl.state.approval_status == ApprovalStatus.REJECTED
        assert ctrl.state.final_status == FinalStatus.CANCELLED
        # Event summary and observation do not describe task as cancelled
        assert "cancelled" not in event.summary.lower()

    def test_approve_twice_rejected(self):
        ctrl = _make_waiting_controller()
        event1 = ctrl.record_approval(True, user_id="alice")
        assert event1.status == ExecutionEventStatus.COMPLETED
        ctrl.execute(AgentDecision(action="finish", done=True))
        assert ctrl.hitl_state == HITLApprovalState.FINAL

        # Second approval attempt must be rejected
        event2 = ctrl.record_approval(True, user_id="alice")
        assert event2.status == ExecutionEventStatus.REJECTED
        assert event2.event_type == ExecutionEventType.CAPABILITY_REJECTED
        # State remains unchanged
        assert ctrl.hitl_state == HITLApprovalState.FINAL
        assert ctrl.state.final_status == FinalStatus.COMPLETED

    def test_reject_twice_rejected(self):
        ctrl = _make_waiting_controller()
        event1 = ctrl.record_approval(False, user_id="alice")
        assert event1.status == ExecutionEventStatus.COMPLETED
        assert ctrl.hitl_state == HITLApprovalState.REJECTED

        # Second rejection attempt must be rejected
        event2 = ctrl.record_approval(False, user_id="alice")
        assert event2.status == ExecutionEventStatus.REJECTED
        assert event2.event_type == ExecutionEventType.CAPABILITY_REJECTED
        # State remains unchanged
        assert ctrl.hitl_state == HITLApprovalState.REJECTED
        assert ctrl.state.final_status == FinalStatus.CANCELLED

    def test_approve_after_reject_rejected(self):
        ctrl = _make_waiting_controller()
        event1 = ctrl.record_approval(False, user_id="alice")
        assert event1.status == ExecutionEventStatus.COMPLETED
        assert ctrl.hitl_state == HITLApprovalState.REJECTED

        # Subsequent approval attempt must be rejected
        event2 = ctrl.record_approval(True, user_id="alice")
        assert event2.status == ExecutionEventStatus.REJECTED
        assert event2.event_type == ExecutionEventType.CAPABILITY_REJECTED
        # State remains REJECTED
        assert ctrl.hitl_state == HITLApprovalState.REJECTED

    def test_reject_after_approve_rejected(self):
        ctrl = _make_waiting_controller()
        event1 = ctrl.record_approval(True, user_id="alice")
        assert event1.status == ExecutionEventStatus.COMPLETED
        ctrl.execute(AgentDecision(action="finish", done=True))
        assert ctrl.hitl_state == HITLApprovalState.FINAL

        # Subsequent rejection attempt must be rejected
        event2 = ctrl.record_approval(False, user_id="alice")
        assert event2.status == ExecutionEventStatus.REJECTED
        assert event2.event_type == ExecutionEventType.CAPABILITY_REJECTED
        # State remains FINAL
        assert ctrl.hitl_state == HITLApprovalState.FINAL


# ===========================================================================
# 2. Service-Level and UI Behavior Tests
# ===========================================================================

class TestServiceAndUIReflectsFinalState:
    """Proves backend service and UI state agree and reject repeated transitions."""

    @pytest.fixture
    def service(self) -> UIBackendService:
        return UIBackendService(db_path=":memory:")

    def _submit_approval_task(self, service: UIBackendService) -> tuple[str, UUID, UUID]:
        _, _, _, token = service.login("alice", "password123")
        sess = service.create_session(token)
        res = service.submit_task(
            token_str=token,
            session_id=sess.session_id,
            prompt="Review scanned inspection report and draft approval note.",
            attachment_path="report.pdf",
        )
        assert res.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL
        return token, sess.session_id, res.task_id

    def test_service_approve_once_completes(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        res = service.record_approval(token, sess_id, task_id, approved=True)

        assert res.hitl_state == HITLApprovalState.FINAL
        assert res.final_status == FinalStatus.COMPLETED
        assert res.status == TaskStatus.COMPLETED
        assert "approved and finalized" in res.result_text.lower()

    def test_service_reject_once_rejects(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        res = service.record_approval(token, sess_id, task_id, approved=False)

        assert res.hitl_state == HITLApprovalState.REJECTED
        assert res.final_status == FinalStatus.CANCELLED
        assert res.status == TaskStatus.CANCELLED
        # Must say rejected and NOT describe task as cancelled in result text
        assert "rejected" in res.result_text.lower()
        assert "cancelled" not in res.result_text.lower()

    def test_service_approve_twice_rejected(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        res1 = service.record_approval(token, sess_id, task_id, approved=True)
        assert res1.hitl_state == HITLApprovalState.FINAL

        # 2nd approval must raise ValueError from runner/controller
        with pytest.raises(ValueError, match="rejected by Controller"):
            service.record_approval(token, sess_id, task_id, approved=True)

    def test_service_reject_twice_rejected(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        res1 = service.record_approval(token, sess_id, task_id, approved=False)
        assert res1.hitl_state == HITLApprovalState.REJECTED

        # 2nd rejection must raise ValueError from runner/controller
        with pytest.raises(ValueError, match="rejected by Controller"):
            service.record_approval(token, sess_id, task_id, approved=False)

    def test_service_approve_after_reject_rejected(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        service.record_approval(token, sess_id, task_id, approved=False)

        # Approve after reject must raise ValueError
        with pytest.raises(ValueError, match="rejected by Controller"):
            service.record_approval(token, sess_id, task_id, approved=True)

    def test_service_reject_after_approve_rejected(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        service.record_approval(token, sess_id, task_id, approved=True)

        # Reject after approve must raise ValueError
        with pytest.raises(ValueError, match="rejected by Controller"):
            service.record_approval(token, sess_id, task_id, approved=False)

    def test_ui_reflects_final_state_on_approve(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        state = {
            "token": token,
            "active_session_id": sess_id,
            "active_task_id": task_id,
            "chat_messages": [],
        }

        (
            new_state,
            chat_up,
            events_up,
            result_up,
            msg_up,
            approve_btn_up,
            reject_btn_up,
            banner_up,
        ) = handle_approval_decision(True, state, service)

        # Controls must be hidden and disabled
        assert approve_btn_up["visible"] is False
        assert approve_btn_up["interactive"] is False
        assert reject_btn_up["visible"] is False
        assert reject_btn_up["interactive"] is False
        assert banner_up["visible"] is False

        # Status message clearly shows approved
        assert msg_up["visible"] is True
        assert "Approved" in msg_up["value"]
        assert new_state["active_task_id"] is None
        assert len(new_state["chat_messages"]) == 2

    def test_ui_reflects_final_state_on_reject(self, service: UIBackendService):
        token, sess_id, task_id = self._submit_approval_task(service)
        state = {
            "token": token,
            "active_session_id": sess_id,
            "active_task_id": task_id,
            "chat_messages": [],
        }

        (
            new_state,
            chat_up,
            events_up,
            result_up,
            msg_up,
            approve_btn_up,
            reject_btn_up,
            banner_up,
        ) = handle_approval_decision(False, state, service)

        # Controls must be hidden and disabled
        assert approve_btn_up["visible"] is False
        assert approve_btn_up["interactive"] is False
        assert reject_btn_up["visible"] is False
        assert reject_btn_up["interactive"] is False
        assert banner_up["visible"] is False

        # Status message clearly says Rejected and NOT cancelled
        assert msg_up["visible"] is True
        assert "Rejected" in msg_up["value"]
        assert "cancelled" not in msg_up["value"].lower()
        assert new_state["active_task_id"] is None
        assert len(new_state["chat_messages"]) == 2
        # Result text in chat must not describe task as cancelled
        assert "cancelled" not in new_state["chat_messages"][1]["content"].lower()

    def test_ui_repeated_approval_decision_rejected_and_controls_disabled(
        self, service: UIBackendService
    ):
        token, sess_id, task_id = self._submit_approval_task(service)
        state = {
            "token": token,
            "active_session_id": sess_id,
            "active_task_id": task_id,
            "chat_messages": [],
        }

        # 1st approval
        new_state, _, _, _, _, _, _, _ = handle_approval_decision(True, state, service)

        # Simulate second click with previous task_id still in state
        repeat_state = dict(state)
        (
            second_state,
            _,
            _,
            _,
            msg_up,
            approve_btn_up,
            reject_btn_up,
            banner_up,
        ) = handle_approval_decision(True, repeat_state, service)

        # Backend rejection was caught, error is shown, controls remain disabled
        assert msg_up["visible"] is True
        assert "Error" in msg_up["value"]
        assert approve_btn_up["visible"] is False
        assert approve_btn_up["interactive"] is False
        assert reject_btn_up["visible"] is False
        assert reject_btn_up["interactive"] is False

