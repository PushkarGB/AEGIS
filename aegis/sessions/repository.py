"""Abstract repository interfaces for session and task persistence.

Concrete implementations (e.g. SQLite) satisfy these contracts.
Service code and tests depend only on these abstractions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from .models import SessionRecord, SessionStatus, TaskRecord, TaskStatus


class NotFoundError(Exception):
    """Raised when a requested session or task does not exist."""


class SessionIsolationError(Exception):
    """Raised when a task is accessed via a session it does not belong to."""


class SessionRepository(ABC):
    """Persistence contract for session lifecycle."""

    @abstractmethod
    def create_session(
        self,
        user_id: str,
        session_id: UUID | None = None,
    ) -> SessionRecord:
        """Insert a new active session and return the persisted record.

        Args:
            user_id: Identity of the user who owns the session.
            session_id: Optional explicit UUID; generated if omitted.

        Returns:
            The persisted :class:`SessionRecord`.
        """

    @abstractmethod
    def get_session(self, session_id: UUID) -> SessionRecord:
        """Return the session record for *session_id*.

        Raises:
            NotFoundError: If no session with that ID exists.
        """

    @abstractmethod
    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return all sessions for *user_id*, newest first.

        Returns an empty list when the user has no sessions.
        """

    @abstractmethod
    def update_session_status(
        self,
        session_id: UUID,
        status: SessionStatus,
    ) -> SessionRecord:
        """Update the session status and refresh *updated_at*.

        Raises:
            NotFoundError: If no session with that ID exists.
        """


class TaskRepository(ABC):
    """Persistence contract for task lifecycle."""

    @abstractmethod
    def create_task(
        self,
        session_id: UUID,
        user_id: str,
        workflow_id: str | None = None,
        task_id: UUID | None = None,
    ) -> TaskRecord:
        """Insert a new pending task and return the persisted record.

        Args:
            session_id: Session that owns this task.
            user_id: Identity of the user who owns the session.
            workflow_id: Optional workflow identifier (matches ``selected_skill``
                in ``TaskState``). May be ``None`` when not yet determined.
            task_id: Optional explicit UUID; generated if omitted.

        Returns:
            The persisted :class:`TaskRecord`.
        """

    @abstractmethod
    def get_task(self, task_id: UUID) -> TaskRecord:
        """Return the task record for *task_id*.

        Raises:
            NotFoundError: If no task with that ID exists.
        """

    @abstractmethod
    def update_task_status(
        self,
        task_id: UUID,
        status: TaskStatus,
    ) -> TaskRecord:
        """Update the task status.

        Raises:
            NotFoundError: If no task with that ID exists.
        """

    @abstractmethod
    def get_tasks_for_session(
        self,
        session_id: UUID,
        user_id: str,
    ) -> list[TaskRecord]:
        """Return all tasks for *session_id* owned by *user_id*, newest first.

        Returns an empty list when no tasks exist.
        """
