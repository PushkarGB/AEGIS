"""Auth-aware session service facade.

``AuthorizedSessionService`` wraps the existing :class:`~aegis.sessions.service.SessionService`
and enforces RBAC permission checks *before* delegating to the inner service.

The inner ``SessionService`` still enforces user-isolation (cross-user access
raises ``NotFoundError``).  This layer adds the auth check *on top* — so
cross-user session access fails at both the auth and isolation layers.

Authorization is never delegated to the UI layer.
"""

from __future__ import annotations

from uuid import UUID

from aegis.auth.authorization import Permission, require_permission
from aegis.auth.exceptions import AuthorizationError
from aegis.auth.models import UserIdentity, UserRole

from .models import SessionRecord, SessionStatus, TaskRecord, TaskStatus
from .repository import NotFoundError, SessionIsolationError
from .service import SessionService


class AuthorizedSessionService:
    """Auth-enforcing facade over :class:`~aegis.sessions.service.SessionService`.

    Every public method accepts a resolved :class:`~aegis.auth.models.UserIdentity`
    (produced by ``AuthService.require_user`` or a guard).  Callers must resolve
    identity via the auth layer before calling these methods.

    Permission checks are performed before delegation; the inner service
    enforces user-isolation regardless.

    Args:
        session_service: The wrapped ``SessionService`` instance.
    """

    def __init__(self, session_service: SessionService) -> None:
        self._inner = session_service

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def create_session(self, user: UserIdentity) -> SessionRecord:
        """Create a session for *user* after checking ``CREATE_OWN_SESSION``.

        Raises:
            AuthorizationError: If *user* lacks ``CREATE_OWN_SESSION``.
        """
        require_permission(user, Permission.CREATE_OWN_SESSION)
        return self._inner.create_session(user_id=user.user_id)

    def get_session(self, session_id: UUID, user: UserIdentity) -> SessionRecord:
        """Return a session owned by *user* after checking ``ACCESS_OWN_SESSION``.

        Raises:
            AuthorizationError: If *user* lacks ``ACCESS_OWN_SESSION``.
            NotFoundError: If the session does not exist or belongs to another user.
        """
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return self._inner.get_session(session_id, user_id=user.user_id)

    def list_sessions(self, user: UserIdentity) -> list[SessionRecord]:
        """List sessions visible to *user*.

        - USER callers: returns only *user*'s own sessions (``ACCESS_OWN_SESSION``).
        - ADMIN callers: returns all sessions across all users (``VIEW_ALL_SESSIONS``).

        Raises:
            AuthorizationError: If *user* lacks the required permission.
        """
        if user.role == UserRole.ADMIN:
            require_permission(user, Permission.VIEW_ALL_SESSIONS)
            return self._inner.list_sessions(user_id=None)
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return self._inner.list_sessions(user_id=user.user_id)

    def close_session(self, session_id: UUID, user: UserIdentity) -> SessionRecord:
        """Close a session owned by *user* after checking ``ACCESS_OWN_SESSION``.

        Raises:
            AuthorizationError: If *user* lacks ``ACCESS_OWN_SESSION``.
            NotFoundError: If the session does not exist or belongs to another user.
        """
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return self._inner.close_session(session_id, user_id=user.user_id)

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    def create_task(
        self,
        session_id: UUID,
        user: UserIdentity,
        workflow_id: str | None = None,
    ) -> TaskRecord:
        """Create a task within *session_id* after checking ``SUBMIT_TASK``.

        Raises:
            AuthorizationError: If *user* lacks ``SUBMIT_TASK``.
            NotFoundError: If the session does not exist or belongs to another user.
        """
        require_permission(user, Permission.SUBMIT_TASK)
        return self._inner.create_task(
            session_id=session_id,
            user_id=user.user_id,
            workflow_id=workflow_id,
        )

    def get_task(self, task_id: UUID, session_id: UUID, user: UserIdentity) -> TaskRecord:
        """Return the task after checking ``ACCESS_OWN_SESSION``.

        Raises:
            AuthorizationError: If *user* lacks ``ACCESS_OWN_SESSION``.
            NotFoundError: If the task does not exist.
            SessionIsolationError: If the task belongs to a different session.
        """
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return self._inner.get_task(task_id, session_id)

    def update_task_status(
        self,
        task_id: UUID,
        session_id: UUID,
        status: TaskStatus,
        user: UserIdentity,
    ) -> TaskRecord:
        """Update task status after checking ``ACCESS_OWN_SESSION``.

        Raises:
            AuthorizationError: If *user* lacks ``ACCESS_OWN_SESSION``.
            NotFoundError: If the task does not exist.
            SessionIsolationError: If the task belongs to a different session.
        """
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return self._inner.update_task_status(task_id, session_id, status)
