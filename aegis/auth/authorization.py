"""Role-based permission definitions for the AEGIS prototype.

Permissions are modelled as a static, deterministic mapping from ``UserRole``
to a ``frozenset[Permission]``.  Authorization checks are enforced in backend
service-layer guards — not in the UI.

Permission model
----------------
USER
    create_own_session, access_own_session, submit_task, upload_file,
    view_own_events, interact_hitl

ADMIN
    All USER permissions, plus:
    view_all_sessions, view_all_audit, access_system_status,
    access_network_monitor, access_model_health
"""

from __future__ import annotations

from enum import StrEnum

from .exceptions import AuthorizationError
from .models import UserIdentity, UserRole


class Permission(StrEnum):
    """Capability identifiers checked at service-layer boundaries."""

    # USER-level permissions
    CREATE_OWN_SESSION = "create_own_session"
    ACCESS_OWN_SESSION = "access_own_session"
    SUBMIT_TASK = "submit_task"
    UPLOAD_FILE = "upload_file"
    VIEW_OWN_EVENTS = "view_own_events"
    INTERACT_HITL = "interact_hitl"

    # ADMIN-only permissions
    VIEW_ALL_SESSIONS = "view_all_sessions"
    VIEW_ALL_AUDIT = "view_all_audit"
    ACCESS_SYSTEM_STATUS = "access_system_status"
    ACCESS_NETWORK_MONITOR = "access_network_monitor"
    ACCESS_MODEL_HEALTH = "access_model_health"


# ---------------------------------------------------------------------------
# Role → permission mapping
# ---------------------------------------------------------------------------

_USER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.CREATE_OWN_SESSION,
        Permission.ACCESS_OWN_SESSION,
        Permission.SUBMIT_TASK,
        Permission.UPLOAD_FILE,
        Permission.VIEW_OWN_EVENTS,
        Permission.INTERACT_HITL,
    }
)

_ADMIN_PERMISSIONS: frozenset[Permission] = _USER_PERMISSIONS | frozenset(
    {
        Permission.VIEW_ALL_SESSIONS,
        Permission.VIEW_ALL_AUDIT,
        Permission.ACCESS_SYSTEM_STATUS,
        Permission.ACCESS_NETWORK_MONITOR,
        Permission.ACCESS_MODEL_HEALTH,
    }
)

_ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.USER: _USER_PERMISSIONS,
    UserRole.ADMIN: _ADMIN_PERMISSIONS,
}


def get_permissions(role: UserRole) -> frozenset[Permission]:
    """Return the complete permission set for *role*."""
    return _ROLE_PERMISSIONS[role]


def has_permission(user: UserIdentity, permission: Permission) -> bool:
    """Return ``True`` if *user* holds *permission*."""
    return permission in _ROLE_PERMISSIONS.get(user.role, frozenset())


def require_permission(user: UserIdentity, permission: Permission) -> None:
    """Assert that *user* holds *permission*.

    Raises:
        AuthorizationError: If *user* does not hold *permission*.  The error
            carries ``required_permission``, ``actual_role``, and ``user_id``
            attributes for structured handling.
    """
    if not has_permission(user, permission):
        raise AuthorizationError(
            f"User '{user.user_id}' (role={user.role!r}) "
            f"lacks required permission '{permission}'",
            required_permission=str(permission),
            actual_role=str(user.role),
            user_id=user.user_id,
        )
