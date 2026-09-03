"""Tests for the UIBackendService application facade.

Validates:
- Authentication (login success, bad password, unknown user, logout).
- Session creation, listing, retrieval, and closure.
- Deterministic task execution and event stream publication.
- Human-in-the-loop (HITL) approval and rejection paths.
- Admin dashboard, users, sessions, audit, network, and model health queries.
- Strict RBAC denial when a USER attempts ADMIN operations.
"""

from __future__ import annotations

from uuid import uuid4
import pytest

from aegis.auth.exceptions import AuthenticationError, AuthorizationError
from aegis.auth.models import UserRole
from aegis.orchestration.hitl import HITLApprovalState
from aegis.schemas import FinalStatus
from aegis.sessions import TaskStatus
from aegis.ui.service import UIBackendService, UITaskResult


@pytest.fixture
def ui_service() -> UIBackendService:
    """Provide a fresh UIBackendService instance backed by in-memory storage."""
    return UIBackendService(db_path=":memory:")


# ---------------------------------------------------------------------------
# 1. Authentication Tests
# ---------------------------------------------------------------------------

def test_login_success_user(ui_service: UIBackendService):
    success, msg, user, token = ui_service.login("alice", "password123")
    assert success is True
    assert user is not None
    assert user.username == "alice"
    assert user.role == UserRole.USER
    assert token is not None


def test_login_success_admin(ui_service: UIBackendService):
    success, msg, user, token = ui_service.login("admin", "adminpass")
    assert success is True
    assert user is not None
    assert user.username == "admin"
    assert user.role == UserRole.ADMIN
    assert token is not None


def test_login_failure_bad_password(ui_service: UIBackendService):
    success, msg, user, token = ui_service.login("alice", "wrongpass")
    assert success is False
    assert user is None
    assert token is None
    assert "invalid" in msg.lower()


def test_login_failure_unknown_user(ui_service: UIBackendService):
    success, msg, user, token = ui_service.login("charlie", "password123")
    assert success is False
    assert user is None
    assert token is None


def test_logout_revokes_token(ui_service: UIBackendService):
    _, _, _, token = ui_service.login("alice", "password123")
    assert ui_service.get_current_user(token) is not None

    ui_service.logout(token)
    assert ui_service.get_current_user(token) is None


# ---------------------------------------------------------------------------
# 2. Session Management Tests
# ---------------------------------------------------------------------------

def test_session_lifecycle(ui_service: UIBackendService):
    _, _, _, token = ui_service.login("alice", "password123")

    # Create session
    sess1 = ui_service.create_session(token)
    assert sess1.user_id == "user-alice-0001"
    assert sess1.status == "active"

    # List sessions
    sessions = ui_service.list_sessions(token)
    assert len(sessions) == 1
    assert sessions[0].session_id == sess1.session_id

    # Get session
    fetched = ui_service.get_session(token, sess1.session_id)
    assert fetched.session_id == sess1.session_id

    # Close session
    closed = ui_service.close_session(token, sess1.session_id)
    assert closed.status == "closed"


def test_session_unauthenticated_raises(ui_service: UIBackendService):
    with pytest.raises(AuthenticationError):
        ui_service.create_session("invalid-token")

    with pytest.raises(AuthenticationError):
        ui_service.list_sessions("invalid-token")


# ---------------------------------------------------------------------------
# 3. Deterministic Task Execution & HITL Tests
# ---------------------------------------------------------------------------

def test_computation_task_runs_to_completion(ui_service: UIBackendService):
    _, _, _, token = ui_service.login("alice", "password123")
    sess = ui_service.create_session(token)

    result: UITaskResult = ui_service.submit_task(
        token_str=token,
        session_id=sess.session_id,
        prompt="Calculate the average measured thickness from equipment readings.",
        attachment_path="inspection.xlsx",
    )

    assert result.session_id == sess.session_id
    assert result.final_status == FinalStatus.COMPLETED
    assert result.status == TaskStatus.COMPLETED
    assert result.hitl_state is None
    assert len(result.events) > 0
    assert "average measured thickness" in result.result_text.lower()
    assert len(result.artifact_paths) > 0


def test_approval_workflow_pauses_for_hitl_and_approves(ui_service: UIBackendService):
    _, _, _, token = ui_service.login("alice", "password123")
    sess = ui_service.create_session(token)

    # 1. Submit approval note task
    result: UITaskResult = ui_service.submit_task(
        token_str=token,
        session_id=sess.session_id,
        prompt="Review scanned inspection report and prepare approval note.",
        attachment_path="report.pdf",
    )

    # Must pause at WAITING_FOR_APPROVAL
    assert result.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL
    assert result.status == TaskStatus.RUNNING
    assert "awaiting human approval" in result.result_text.lower()

    # 2. Operator records approval
    approval_result: UITaskResult = ui_service.record_approval(
        token_str=token,
        session_id=sess.session_id,
        task_id=result.task_id,
        approved=True,
    )

    assert approval_result.hitl_state == HITLApprovalState.FINAL
    assert approval_result.final_status == FinalStatus.COMPLETED
    assert approval_result.status == TaskStatus.COMPLETED
    assert "approved and finalized" in approval_result.result_text.lower()


def test_approval_workflow_rejects(ui_service: UIBackendService):
    _, _, _, token = ui_service.login("alice", "password123")
    sess = ui_service.create_session(token)

    # 1. Submit approval note task
    result: UITaskResult = ui_service.submit_task(
        token_str=token,
        session_id=sess.session_id,
        prompt="Review inspection report for approval clearance.",
        attachment_path="report.pdf",
    )
    assert result.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL

    # 2. Operator rejects
    rejection_result: UITaskResult = ui_service.record_approval(
        token_str=token,
        session_id=sess.session_id,
        task_id=result.task_id,
        approved=False,
    )

    assert rejection_result.hitl_state == HITLApprovalState.REJECTED
    assert rejection_result.final_status == FinalStatus.CANCELLED
    assert rejection_result.status == TaskStatus.CANCELLED
    assert "rejected" in rejection_result.result_text.lower()


# ---------------------------------------------------------------------------
# 4. Admin Operations & RBAC Verification Tests
# ---------------------------------------------------------------------------

def test_admin_dashboard_metrics(ui_service: UIBackendService):
    _, _, _, admin_token = ui_service.login("admin", "adminpass")
    _, _, _, user_token = ui_service.login("alice", "password123")

    # Create a session and task to have metrics
    sess = ui_service.create_session(user_token)
    ui_service.submit_task(user_token, sess.session_id, "Test task calculation")

    dash = ui_service.get_admin_dashboard(admin_token)
    assert dash["total_users"] == 3
    assert dash["total_sessions"] >= 1
    assert dash["total_audit_events"] > 0
    assert dash["total_models"] > 0
    assert "network_observations" in dash


def test_admin_users_and_sessions(ui_service: UIBackendService):
    _, _, _, admin_token = ui_service.login("admin", "adminpass")
    users = ui_service.get_admin_users(admin_token)
    assert len(users) == 3
    assert {u["username"] for u in users} == {"alice", "bob", "admin"}

    sessions = ui_service.get_admin_sessions(admin_token)
    assert isinstance(sessions, list)


def test_admin_audit_logs(ui_service: UIBackendService):
    _, _, _, admin_token = ui_service.login("admin", "adminpass")
    _, _, _, user_token = ui_service.login("alice", "password123")

    sess = ui_service.create_session(user_token)
    ui_service.submit_task(user_token, sess.session_id, "Inspect calculation")

    logs = ui_service.get_admin_audit_logs(admin_token, limit=50)
    assert len(logs) > 0
    assert any("task_started" in l["event_type"] for l in logs)


def test_admin_network_and_model_health(ui_service: UIBackendService):
    _, _, _, admin_token = ui_service.login("admin", "adminpass")

    net_info = ui_service.get_admin_network(admin_token)
    assert "summary" in net_info
    assert "observations" in net_info

    models = ui_service.get_admin_model_health(admin_token)
    assert len(models) > 0
    assert any(m["model_id"] for m in models)


def test_user_denied_admin_operations(ui_service: UIBackendService):
    _, _, _, user_token = ui_service.login("alice", "password123")

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_dashboard(user_token)

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_users(user_token)

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_sessions(user_token)

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_audit_logs(user_token)

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_network(user_token)

    with pytest.raises(AuthorizationError):
        ui_service.get_admin_model_health(user_token)
