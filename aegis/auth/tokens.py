"""Opaque token store for the AEGIS prototype.

Issues, validates, and revokes stateful in-memory tokens.  Each token is a
UUID4 hex string (opaque — no encoded claims).  The store uses explicit TTL
expiry; tokens are validated on each call to :meth:`TokenStore.validate`.

This is an in-memory prototype store only.  In production it would be backed
by a server-side session database or replaced by a proper session service.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import AuthToken, UserIdentity


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokenStore:
    """Thread-safe in-memory opaque token store.

    Tokens are issued as UUID4 hex strings.  They expire after *ttl_seconds*
    and can be explicitly revoked.  The store is the single source of truth
    for token validity — callers must not cache or infer validity from the
    token string itself.

    Args:
        default_ttl_seconds: Lifetime of each issued token in seconds.
            Defaults to 3600 (1 hour).
    """

    def __init__(self, default_ttl_seconds: int = 3600) -> None:
        self._default_ttl = default_ttl_seconds
        self._tokens: dict[str, AuthToken] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        user: UserIdentity,
        ttl_seconds: int | None = None,
    ) -> AuthToken:
        """Issue a new opaque token for *user*.

        Args:
            user: The resolved identity to associate with the token.
            ttl_seconds: Override TTL; uses ``default_ttl_seconds`` if omitted.

        Returns:
            A new :class:`~aegis.auth.models.AuthToken`.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = _utc_now()
        token_str = uuid4().hex
        auth_token = AuthToken(
            token=token_str,
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        with self._lock:
            self._tokens[token_str] = auth_token
        return auth_token

    def validate(self, token_str: str) -> AuthToken | None:
        """Return the :class:`~aegis.auth.models.AuthToken` if valid.

        Returns ``None`` if the token is unknown, expired, or revoked.
        Does not remove expired tokens (lazy cleanup).
        """
        with self._lock:
            auth_token = self._tokens.get(token_str)
        if auth_token is None:
            return None
        if _utc_now() > auth_token.expires_at:
            return None
        return auth_token

    def revoke(self, token_str: str) -> None:
        """Revoke a token immediately.

        Silently ignores unknown or already-expired tokens.
        """
        with self._lock:
            self._tokens.pop(token_str, None)

    def purge_expired(self) -> int:
        """Remove all expired tokens and return the count removed."""
        now = _utc_now()
        with self._lock:
            expired = [t for t, auth in self._tokens.items() if now > auth.expires_at]
            for t in expired:
                del self._tokens[t]
        return len(expired)
