"""Prototype RBAC — authentication and authorization for the AEGIS prototype.

This package provides:

- ``UserRole`` / ``UserIdentity`` / ``AuthToken`` — identity data contracts
- ``AuthService`` — login, logout, token resolution, guarded accessors
- ``PrototypeCredentialStore`` — hardcoded prototype credentials
- ``TokenStore`` — in-memory opaque token lifecycle
- ``Permission`` / ``has_permission`` / ``require_permission`` — RBAC checks
- ``SessionGuard`` / ``AuditGuard`` / ``SystemGuard`` — service-layer guards
- ``AuthenticationError`` / ``AuthorizationError`` — typed exceptions

Authorization is enforced in service-layer guards.
Do not rely on UI visibility for security.
"""

from .authorization import Permission, get_permissions, has_permission, require_permission
from .credentials import PrototypeCredentialStore
from .exceptions import AuthenticationError, AuthorizationError
from .guards import AuditGuard, SessionGuard, SystemGuard
from .models import AuthToken, LoginRequest, LoginResult, UserIdentity, UserRole
from .service import AuthService
from .tokens import TokenStore

__all__ = [
    # Exceptions
    "AuthenticationError",
    "AuthorizationError",
    # Models
    "AuthToken",
    "LoginRequest",
    "LoginResult",
    "UserIdentity",
    "UserRole",
    # Credentials
    "PrototypeCredentialStore",
    # Tokens
    "TokenStore",
    # Authorization
    "Permission",
    "get_permissions",
    "has_permission",
    "require_permission",
    # Service
    "AuthService",
    # Guards
    "AuditGuard",
    "SessionGuard",
    "SystemGuard",
]
