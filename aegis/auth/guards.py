"""Service-layer authorization guards for the AEGIS prototype.

Guards combine :class:`~aegis.auth.service.AuthService` with
:func:`~aegis.auth.authorization.require_permission` to enforce access control
at the service boundary.  They are the definitive enforcement point —
UI visibility must never substitute for these checks.

Available guards
----------------
``SessionGuard``   — controls who can read session data
``AuditGuard``     — restricts all-audit access to ADMIN
``SystemGuard``    — restricts system-status, network-monitor, and model-health to ADMIN
"""

from __future__ import annotations

from .authorization import Permission, require_permission
from .exceptions import AuthorizationError
from .models import UserIdentity
from .service import AuthService


class SessionGuard:
    """Enforces session-access permissions at the service boundary.

    Args:
        auth_service: The shared :class:`~aegis.auth.service.AuthService`.
    """

    def __init__(self, auth_service: AuthService) -> None:
        self._auth = auth_service

    def require_own_session_access(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``ACCESS_OWN_SESSION`` permission.

        Raises:
            AuthenticationError: Invalid/expired token.
            AuthorizationError: Caller lacks ``ACCESS_OWN_SESSION``.
        """
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.ACCESS_OWN_SESSION)
        return user

    def require_create_session(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``CREATE_OWN_SESSION`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.CREATE_OWN_SESSION)
        return user

    def require_all_sessions_access(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``VIEW_ALL_SESSIONS`` permission.

        Only ADMIN callers hold this permission.

        Raises:
            AuthenticationError: Invalid/expired token.
            AuthorizationError: Caller lacks ``VIEW_ALL_SESSIONS``.
        """
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.VIEW_ALL_SESSIONS)
        return user

    def require_submit_task(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``SUBMIT_TASK`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.SUBMIT_TASK)
        return user

    def require_upload_file(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``UPLOAD_FILE`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.UPLOAD_FILE)
        return user

    def require_view_own_events(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``VIEW_OWN_EVENTS`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.VIEW_OWN_EVENTS)
        return user

    def require_interact_hitl(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``INTERACT_HITL`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.INTERACT_HITL)
        return user


class AuditGuard:
    """Restricts all-audit record access to ADMIN callers.

    Args:
        auth_service: The shared :class:`~aegis.auth.service.AuthService`.
    """

    def __init__(self, auth_service: AuthService) -> None:
        self._auth = auth_service

    def require_view_all_audit(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``VIEW_ALL_AUDIT`` permission.

        Raises:
            AuthenticationError: Invalid/expired token.
            AuthorizationError: Caller lacks ``VIEW_ALL_AUDIT`` (non-ADMIN).
        """
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.VIEW_ALL_AUDIT)
        return user


class SystemGuard:
    """Restricts system-status, network-monitor, and model-health to ADMIN.

    Args:
        auth_service: The shared :class:`~aegis.auth.service.AuthService`.
    """

    def __init__(self, auth_service: AuthService) -> None:
        self._auth = auth_service

    def require_system_status(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``ACCESS_SYSTEM_STATUS`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.ACCESS_SYSTEM_STATUS)
        return user

    def require_network_monitor(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``ACCESS_NETWORK_MONITOR`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.ACCESS_NETWORK_MONITOR)
        return user

    def require_model_health(self, token_str: str) -> UserIdentity:
        """Resolve caller and assert ``ACCESS_MODEL_HEALTH`` permission."""
        user = self._auth.require_user(token_str)
        require_permission(user, Permission.ACCESS_MODEL_HEALTH)
        return user
