"""Authentication service facade for the AEGIS prototype.

``AuthService`` is the single entry point for all auth operations:

- Login with username/password → :class:`~aegis.auth.models.LoginResult`
- Logout (token revocation)
- Current-user resolution from an opaque token string
- Guarded accessors that raise on invalid/insufficient credentials

Authorization is enforced in backend service guards, not in the UI.
"""

from __future__ import annotations

from .credentials import PrototypeCredentialStore
from .exceptions import AuthenticationError, AuthorizationError
from .models import LoginRequest, LoginResult, UserIdentity, UserRole
from .tokens import TokenStore


class AuthService:
    """Prototype authentication and current-user resolution service.

    Combines :class:`~aegis.auth.credentials.PrototypeCredentialStore` with a
    :class:`~aegis.auth.tokens.TokenStore` to provide login, logout, and
    token-to-identity resolution.

    Args:
        credential_store: Credential verifier.  Defaults to the prototype store.
        token_store: Token issuer/validator.  Defaults to a new in-memory store
            with 3600-second TTL.
    """

    def __init__(
        self,
        credential_store: PrototypeCredentialStore | None = None,
        token_store: TokenStore | None = None,
    ) -> None:
        self._credentials = credential_store or PrototypeCredentialStore()
        self._tokens = token_store or TokenStore()

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> LoginResult:
        """Authenticate *username*/*password* and return a :class:`LoginResult`.

        On success the result contains an :class:`~aegis.auth.models.AuthToken`.
        On failure the result carries a human-readable ``error`` message.
        This method never raises — callers inspect ``LoginResult.success``.
        """
        identity = self._credentials.verify(username, password)
        if identity is None:
            return LoginResult(
                success=False,
                error="Invalid username or password.",
            )
        token = self._tokens.issue(identity)
        return LoginResult(success=True, token=token)

    def logout(self, token_str: str) -> None:
        """Revoke *token_str*.

        Silently ignores unknown or already-expired tokens — idempotent logout.
        """
        self._tokens.revoke(token_str)

    # ------------------------------------------------------------------
    # Current-user resolution
    # ------------------------------------------------------------------

    def resolve_current_user(self, token_str: str) -> UserIdentity | None:
        """Resolve *token_str* to a :class:`~aegis.auth.models.UserIdentity`.

        Returns ``None`` if the token is missing, expired, or revoked.
        Does not raise.
        """
        auth_token = self._tokens.validate(token_str)
        if auth_token is None:
            return None
        identity = self._credentials.lookup(auth_token.username)
        if identity is None:
            # Token was valid but user no longer exists in credential store.
            return None
        return identity

    # ------------------------------------------------------------------
    # Guarded resolution (raises on failure)
    # ------------------------------------------------------------------

    def require_user(self, token_str: str) -> UserIdentity:
        """Resolve *token_str* or raise :class:`~aegis.auth.exceptions.AuthenticationError`.

        Raises:
            AuthenticationError: If the token is invalid, expired, or revoked.
        """
        identity = self.resolve_current_user(token_str)
        if identity is None:
            raise AuthenticationError(
                "Authentication required: token is invalid or has expired."
            )
        return identity

    def require_role(self, token_str: str, required_role: UserRole) -> UserIdentity:
        """Resolve *token_str* and assert the caller holds *required_role*.

        ``ADMIN`` satisfies a requirement for ``USER`` because ADMIN is a
        superset of USER permissions.

        Raises:
            AuthenticationError: If the token is invalid or expired.
            AuthorizationError: If the caller's role is insufficient.
        """
        identity = self.require_user(token_str)
        if required_role == UserRole.USER:
            # Both USER and ADMIN satisfy a USER requirement.
            return identity
        if identity.role != required_role:
            raise AuthorizationError(
                f"Role '{required_role}' required; caller has role '{identity.role}'.",
                actual_role=str(identity.role),
                user_id=identity.user_id,
            )
        return identity
