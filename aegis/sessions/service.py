"""Session service facade.

Coordinates session and task repositories while enforcing user-isolation
invariants. All public methods require a ``user_id`` argument; cross-user
access raises :class:`NotFoundError` rather than leaking another user's data.
"""

from __future__ import annotations

from uuid import UUID

from .models import SessionRecord, SessionStatus, TaskRecord, TaskStatus
from .repository import (
    NotFoundError,
    SessionIsolationError,
    SessionRepository,
    TaskRepository,
)


class SessionService:
    """Coordinates session and task lifecycle with user isolation.

    Args:
        session_repo: A :class:`SessionRepository` implementation.
        task_repo: A :class:`TaskRepository` implementation.
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        task_repo: TaskRepository,
    ) -> None:
        self._sessions = session_repo
        self._tasks = task_repo

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def create_session(self, user_id: str) -> SessionRecord:
        """Create and persist a new active session for *user_id*."""
        return self._sessions.create_session(user_id=user_id)

    def get_session(self, session_id: UUID, user_id: str) -> SessionRecord:
        """Return the session, enforcing that it belongs to *user_id*.

        Raises:
            NotFoundError: If the session does not exist or belongs to a
                different user (identical error to avoid data leakage).
        """
        record = self._sessions.get_session(session_id)
        if record.user_id != user_id:
            raise NotFoundError(f"Session {session_id!s} not found")
        return record

    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return all sessions for *user_id*, newest first."""
        return self._sessions.list_sessions(user_id=user_id)

    def close_session(self, session_id: UUID, user_id: str) -> SessionRecord:
        """Close the session, enforcing ownership.

        Raises:
            NotFoundError: If the session does not exist or belongs to a
                different user.
        """
        self.get_session(session_id, user_id)  # ownership check
        return self._sessions.update_session_status(
            session_id=session_id,
            status=SessionStatus.CLOSED,
        )

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    def create_task(
        self,
        session_id: UUID,
        user_id: str,
        workflow_id: str | None = None,
    ) -> TaskRecord:
        """Create a task within *session_id*, verifying session ownership.

        Raises:
            NotFoundError: If the session does not exist or belongs to a
                different user.
        """
        self.get_session(session_id, user_id)  # ownership check
        return self._tasks.create_task(
            session_id=session_id,
            user_id=user_id,
            workflow_id=workflow_id,
        )

    def get_task(self, task_id: UUID, session_id: UUID) -> TaskRecord:
        """Return the task, enforcing that it belongs to *session_id*.

        Raises:
            NotFoundError: If the task does not exist.
            SessionIsolationError: If the task exists but belongs to a
                different session.
        """
        record = self._tasks.get_task(task_id)
        if record.session_id != session_id:
            raise SessionIsolationError(
                f"Task {task_id!s} does not belong to session {session_id!s}"
            )
        return record

    def update_task_status(
        self,
        task_id: UUID,
        session_id: UUID,
        status: TaskStatus,
    ) -> TaskRecord:
        """Update task status after verifying session membership.

        Raises:
            NotFoundError: If the task does not exist.
            SessionIsolationError: If the task belongs to a different session.
        """
        self.get_task(task_id, session_id)  # isolation check
        return self._tasks.update_task_status(task_id=task_id, status=status)
