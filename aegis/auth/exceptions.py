"""Authentication and authorization exceptions for the AEGIS prototype.

These are raised by service-layer guards when a caller lacks a valid token
or has insufficient role/permission. UI code must not substitute visibility
checks for these server-side enforcements.
"""

from __future__ import annotations


class AuthenticationError(Exception):
    """Raised when a caller cannot be authenticated.

    Causes: missing token, expired token, revoked token, or unknown token.
    """

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message)
        self.message = message


class AuthorizationError(Exception):
    """Raised when an authenticated caller lacks a required permission.

    Attributes:
        required_permission: The ``Permission`` value that was required.
        actual_role: The ``UserRole`` of the authenticated caller.
        user_id: The caller's user identifier.
    """

    def __init__(
        self,
        message: str,
        *,
        required_permission: str | None = None,
        actual_role: str | None = None,
        user_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.required_permission = required_permission
        self.actual_role = actual_role
        self.user_id = user_id
