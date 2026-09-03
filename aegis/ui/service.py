"""Application service facade for the AEGIS Gradio UI.

All Gradio callbacks MUST call methods on this facade. UI components do not
execute business logic, enforce authorization rules directly, or directly mutate
storage. Authorization is verified by backend guards before every operation.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from aegis.audit import AuditService, AuthorizedAuditService
from aegis.auth import (
    AuthService,
    AuditGuard,
    SessionGuard,
    SystemGuard,
    UserIdentity,
    UserRole,
)
from aegis.auth.credentials import _PROTOTYPE_CREDENTIALS, PrototypeCredentialStore
from aegis.auth.exceptions import AuthenticationError, AuthorizationError
from aegis.config import load_config
from aegis.events import ExecutionEvent, ExecutionEventPublisher
from aegis.orchestration.hitl import HITLApprovalState
from aegis.router import ModelRegistry
from aegis.schemas import FinalStatus
from aegis.security import (
    AuthorizedNetworkMonitor,
    InMemoryNetworkCollector,
    StandardNetworkMonitor,
)
from aegis.sessions import AuthorizedSessionService, SessionRecord, SessionService, TaskRecord, TaskStatus
from aegis.sessions.sqlite_store import SqliteStoreFactory
from aegis.ui.event_stream import SessionEventCollector, format_progressive_events
from aegis.ui.runner import DeterministicTaskRunner, ExecutionRunResult


@dataclass(frozen=True)
class UITaskResult:
    """Consolidated task execution result returned to UI handlers."""

    task_id: UUID
    session_id: UUID
    status: TaskStatus
    final_status: FinalStatus
    hitl_state: HITLApprovalState | None
    events: list[ExecutionEvent]
    result_text: str
    artifact_paths: list[str]


@dataclass(frozen=True)
class UIStreamUpdate:
    """Incremental streaming update yielded during task execution."""

    events_markdown: str
    events: list[ExecutionEvent] = field(default_factory=list)
    is_final: bool = False
    result: UITaskResult | None = None


class UIBackendService:
    """Central backend coordinator for the AEGIS Gradio Workbench.

    Encapsulates Auth, Sessions, Deterministic Execution, HITL, Audit,
    Network Monitoring, and Model Registry behind explicit service methods.
    """

    def __init__(
        self,
        auth_service: AuthService | None = None,
        session_service: AuthorizedSessionService | None = None,
        audit_service: AuditService | None = None,
        network_monitor: AuthorizedNetworkMonitor | None = None,
        model_registry: ModelRegistry | None = None,
        db_path: str = ":memory:",
    ) -> None:
        # Auth & Guards
        self._auth = auth_service or AuthService()
        self._session_guard = SessionGuard(self._auth)
        self._audit_guard = AuditGuard(self._auth)
        self._system_guard = SystemGuard(self._auth)

        # Persistence & Sessions
        if session_service is None:
            session_repo, task_repo = SqliteStoreFactory.create(db_path=db_path)
            inner_sessions = SessionService(session_repo, task_repo)
            self._sessions = AuthorizedSessionService(inner_sessions)
        else:
            self._sessions = session_service

        # Event stream & Audit
        self._publisher = ExecutionEventPublisher()
        self._audit = audit_service or AuditService()
        self._publisher.subscribe(self._audit)
        self._auth_audit = AuthorizedAuditService(self._audit)

        # Network Monitor
        if network_monitor is None:
            collector = InMemoryNetworkCollector()
            std_monitor = StandardNetworkMonitor(collector=collector)
            self._network = AuthorizedNetworkMonitor(std_monitor)
        else:
            self._network = network_monitor

        # Model Registry
        if model_registry is None:
            config = load_config()
            self._models = ModelRegistry(config.models)
        else:
            self._models = model_registry

        # Deterministic Runner (with 0 pacing for non-streaming; streaming uses default)
        self._runner = DeterministicTaskRunner(
            event_publisher=self._publisher,
            event_pace_seconds=0.0,
        )

    @property
    def event_publisher(self) -> ExecutionEventPublisher:
        """Expose publisher for streaming event collection."""
        return self._publisher

    # ------------------------------------------------------------------
    # Authentication Operations
    # ------------------------------------------------------------------

    def login(
        self, username: str, password: str
    ) -> tuple[bool, str, UserIdentity | None, str | None]:
        """Authenticate user credentials.

        Returns (success, message, UserIdentity | None, token_str | None).
        """
        result = self._auth.login(username=username, password=password)
        if not result.success or result.token is None:
            return False, result.error or "Authentication failed.", None, None

        user = self._auth.resolve_current_user(result.token.token)
        return True, "Login successful.", user, result.token.token

    def logout(self, token_str: str) -> None:
        """Revoke the given session token."""
        self._auth.logout(token_str)

    def get_current_user(self, token_str: str) -> UserIdentity | None:
        """Resolve current caller identity from token."""
        return self._auth.resolve_current_user(token_str)

    # ------------------------------------------------------------------
    # Session Operations
    # ------------------------------------------------------------------

    def create_session(self, token_str: str) -> SessionRecord:
        """Create a new session for the authenticated user."""
        user = self._session_guard.require_create_session(token_str)
        return self._sessions.create_session(user)

    def list_sessions(self, token_str: str) -> list[SessionRecord]:
        """List sessions visible to the caller (own for USER, all for ADMIN)."""
        user = self._auth.require_user(token_str)
        return self._sessions.list_sessions(user)

    def get_session(self, token_str: str, session_id: UUID) -> SessionRecord:
        """Fetch session by ID with ownership verification."""
        user = self._session_guard.require_own_session_access(token_str)
        return self._sessions.get_session(session_id, user)

    def close_session(self, token_str: str, session_id: UUID) -> SessionRecord:
        """Close an active session."""
        user = self._session_guard.require_own_session_access(token_str)
        return self._sessions.close_session(session_id, user)

    # ------------------------------------------------------------------
    # Task Execution & HITL Operations
    # ------------------------------------------------------------------

    def submit_task(
        self,
        token_str: str,
        session_id: UUID,
        prompt: str,
        attachment_path: str | None = None,
    ) -> UITaskResult:
        """Submit a new task for governed execution."""
        user = self._session_guard.require_submit_task(token_str)
        if attachment_path:
            self._session_guard.require_upload_file(token_str)

        # 1. Create task record in persistence
        task_record = self._sessions.create_task(
            session_id=session_id,
            user=user,
        )

        # 2. Update status to RUNNING
        self._sessions.update_task_status(
            task_id=task_record.task_id,
            session_id=session_id,
            status=TaskStatus.RUNNING,
            user=user,
        )

        # 3. Execute through deterministic runner
        run_res = self._runner.start_execution(
            session_id=session_id,
            task_id=task_record.task_id,
            user_id=user.user_id,
            user_goal=prompt,
            attachment_path=attachment_path,
        )

        # 4. Map final status back to task record
        status = TaskStatus.COMPLETED
        if run_res.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL:
            status = TaskStatus.RUNNING
        elif run_res.final_status == FinalStatus.FAILED:
            status = TaskStatus.FAILED
        elif run_res.final_status == FinalStatus.CANCELLED:
            status = TaskStatus.CANCELLED

        self._sessions.update_task_status(
            task_id=task_record.task_id,
            session_id=session_id,
            status=status,
            user=user,
        )

        return UITaskResult(
            task_id=task_record.task_id,
            session_id=session_id,
            status=status,
            final_status=run_res.final_status,
            hitl_state=run_res.hitl_state,
            events=run_res.events,
            result_text=run_res.result_text,
            artifact_paths=run_res.artifact_paths,
        )

    def submit_task_streaming(
        self,
        token_str: str,
        session_id: UUID,
        prompt: str,
        attachment_path: str | None = None,
        poll_interval: float = 0.15,
    ) -> Generator[UIStreamUpdate, None, None]:
        """Submit a task and yield progressive event updates during execution.

        Uses a per-task ``SessionEventCollector`` to isolate events by identity.
        Runs execution in a background thread; yields ``UIStreamUpdate`` objects
        with cumulative event Markdown as events arrive.

        The final yield has ``is_final=True`` and contains the complete ``UITaskResult``.

        Parameters
        ----------
        token_str:
            Caller authentication token.
        session_id:
            Active session to execute within.
        prompt:
            Natural-language task request.
        attachment_path:
            Optional file attachment path.
        poll_interval:
            Seconds between event drain polls (default 0.15s).
        """
        import time
        from aegis.ui.event_stream import MOCK_EVENT_PACE_SECONDS

        user = self._session_guard.require_submit_task(token_str)
        if attachment_path:
            self._session_guard.require_upload_file(token_str)

        # Create task record
        task_record = self._sessions.create_task(
            session_id=session_id,
            user=user,
        )
        self._sessions.update_task_status(
            task_id=task_record.task_id,
            session_id=session_id,
            status=TaskStatus.RUNNING,
            user=user,
        )

        # Create a per-task collector scoped to this user's identity
        collector = SessionEventCollector(
            publisher=self._publisher,
            session_id=session_id,
            task_id=task_record.task_id,
            user_id=user.user_id,
        )

        # Build a separate runner with pacing enabled for streaming
        streaming_runner = DeterministicTaskRunner(
            event_publisher=self._publisher,
            event_pace_seconds=MOCK_EVENT_PACE_SECONDS,
        )

        # Run execution in background thread
        run_result_holder: list[ExecutionRunResult] = []
        error_holder: list[Exception] = []

        def _execute() -> None:
            try:
                result = streaming_runner.start_execution(
                    session_id=session_id,
                    task_id=task_record.task_id,
                    user_id=user.user_id,
                    user_goal=prompt,
                    attachment_path=attachment_path,
                )
                run_result_holder.append(result)
            except Exception as exc:
                error_holder.append(exc)

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

        # Yield progressive updates while execution runs
        all_events: list[ExecutionEvent] = []
        while thread.is_alive():
            time.sleep(poll_interval)
            new_events = collector.drain()
            if new_events:
                all_events.extend(new_events)
                yield UIStreamUpdate(
                    events_markdown=format_progressive_events(all_events),
                    events=list(all_events),
                )

        # Drain any remaining events after thread completes
        thread.join()
        remaining = collector.drain()
        if remaining:
            all_events.extend(remaining)

        collector.unsubscribe()

        # Handle errors
        if error_holder:
            raise error_holder[0]

        run_res = run_result_holder[0]

        # Copy the streaming runner's active controller to the service runner
        # so HITL approval can find it
        ctrl = streaming_runner.get_controller(task_record.task_id)
        if ctrl is not None:
            self._runner._active_controllers[task_record.task_id] = ctrl

        # Map final status
        status = TaskStatus.COMPLETED
        if run_res.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL:
            status = TaskStatus.RUNNING
        elif run_res.final_status == FinalStatus.FAILED:
            status = TaskStatus.FAILED
        elif run_res.final_status == FinalStatus.CANCELLED:
            status = TaskStatus.CANCELLED

        self._sessions.update_task_status(
            task_id=task_record.task_id,
            session_id=session_id,
            status=status,
            user=user,
        )

        final_result = UITaskResult(
            task_id=task_record.task_id,
            session_id=session_id,
            status=status,
            final_status=run_res.final_status,
            hitl_state=run_res.hitl_state,
            events=all_events if all_events else run_res.events,
            result_text=run_res.result_text,
            artifact_paths=run_res.artifact_paths,
        )

        yield UIStreamUpdate(
            events_markdown=format_progressive_events(final_result.events),
            events=final_result.events,
            is_final=True,
            result=final_result,
        )

    def record_approval(
        self,
        token_str: str,
        session_id: UUID,
        task_id: UUID,
        approved: bool,
    ) -> UITaskResult:
        """Record human approval or rejection for an awaiting task."""
        user = self._session_guard.require_interact_hitl(token_str)

        run_res = self._runner.record_approval(
            task_id=task_id,
            user_id=user.user_id,
            approved=approved,
        )

        status = TaskStatus.COMPLETED if approved else TaskStatus.CANCELLED
        self._sessions.update_task_status(
            task_id=task_id,
            session_id=session_id,
            status=status,
            user=user,
        )

        return UITaskResult(
            task_id=task_id,
            session_id=session_id,
            status=status,
            final_status=run_res.final_status,
            hitl_state=run_res.hitl_state,
            events=run_res.events,
            result_text=run_res.result_text,
            artifact_paths=run_res.artifact_paths,
        )

    # ------------------------------------------------------------------
    # Admin Operations (Guarded by RBAC)
    # ------------------------------------------------------------------

    def get_admin_dashboard(self, token_str: str) -> dict[str, Any]:
        """Aggregate high-level system metrics for the Admin Dashboard."""
        user = self._system_guard.require_system_status(token_str)
        sessions = self._sessions.list_sessions(user)
        active_sessions = sum(1 for s in sessions if s.status == "active")

        net_summary = self._network.get_summary(user)
        audit_count = self._audit.total_count
        models = self._models.list_models()

        return {
            "total_users": len(_PROTOTYPE_CREDENTIALS),
            "total_sessions": len(sessions),
            "active_sessions": active_sessions,
            "total_audit_events": audit_count,
            "network_observations": net_summary.total_observations,
            "network_egress_violations": net_summary.external_count,
            "total_models": len(models),
            "available_models": len(self._models.list_models(available_only=True)),
        }

    def get_admin_users(self, token_str: str) -> list[dict[str, Any]]:
        """Return registered users and roles (ADMIN only)."""
        self._auth.require_role(token_str, UserRole.ADMIN)
        return [
            {
                "user_id": entry["user_id"],
                "username": entry["username"],
                "role": str(entry["role"]),
                "display_name": entry["display_name"],
            }
            for entry in _PROTOTYPE_CREDENTIALS
        ]

    def get_admin_sessions(self, token_str: str) -> list[dict[str, Any]]:
        """Return all sessions across all users (ADMIN only)."""
        user = self._session_guard.require_all_sessions_access(token_str)
        sessions = self._sessions.list_sessions(user)
        return [
            {
                "session_id": str(s.session_id),
                "user_id": s.user_id,
                "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "status": str(s.status),
            }
            for s in sessions
        ]

    def get_admin_audit_logs(
        self, token_str: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return execution event audit records (ADMIN only)."""
        user = self._audit_guard.require_view_all_audit(token_str)
        records = self._auth_audit.get_records(user, limit=limit)
        return [
            {
                "sequence": r.sequence,
                "timestamp": r.timestamp.strftime("%H:%M:%S UTC"),
                "event_type": str(r.event_type),
                "status": str(r.status),
                "component": r.component,
                "summary": r.summary,
                "task_id": str(r.task_id)[:8],
                "user_id": r.user_id or "-",
            }
            for r in records
        ]

    def get_admin_network(self, token_str: str) -> dict[str, Any]:
        """Return network monitoring summary and observations (ADMIN only)."""
        user = self._system_guard.require_network_monitor(token_str)
        summary = self._network.get_summary(user)
        observations = self._network.get_observations(user, limit=50)

        obs_list = [
            {
                "timestamp": obs.timestamp.strftime("%H:%M:%S UTC"),
                "direction": str(obs.direction),
                "destination": obs.destination,
                "destination_port": obs.destination_port,
                "protocol": obs.protocol,
                "classification": str(obs.classification),
                "status": str(obs.status),
            }
            for obs in observations
        ]

        return {
            "summary": {
                "total_observations": summary.total_observations,
                "internal_count": summary.internal_count,
                "external_count": summary.external_count,
                "blocked_count": summary.blocked_count,
                "unknown_count": summary.unknown_count,
                "policy_violations": summary.policy_violations,
            },
            "observations": obs_list,
        }

    def get_admin_model_health(self, token_str: str) -> list[dict[str, Any]]:
        """Return registered model status and health (ADMIN only)."""
        self._system_guard.require_model_health(token_str)
        models = self._models.list_models()
        return [
            {
                "model_id": m.id,
                "provider": m.provider,
                "role": ", ".join(m.roles),
                "context_window": m.context_window,
                "available": m.available,
                "health": str(m.health),
                "enabled": m.enabled,
            }
            for m in models
        ]
