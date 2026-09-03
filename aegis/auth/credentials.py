"""Prototype credential store for the AEGIS prototype.

This module contains hardcoded prototype credentials only.  It is explicitly
NOT a production authentication system.  Use ``secrets.compare_digest`` to
avoid timing-based enumeration attacks even in a prototype context.

PROTOTYPE ONLY — replace with a real IAM integration before production use.

Prototype users
---------------
username   password       role
---------  -------------  -----
alice      password123    USER
bob        password123    USER
admin      adminpass      ADMIN
"""

from __future__ import annotations

import secrets
from uuid import uuid4

from .models import UserIdentity, UserRole

# ---------------------------------------------------------------------------
# Prototype credential table
#
# Each entry: (user_id, username, plain_password, role, display_name)
# Passwords are stored as plain strings intentionally — this is a prototype.
# In production this would be replaced by a real IAM/identity service.
# ---------------------------------------------------------------------------

_PROTOTYPE_CREDENTIALS: list[dict[str, str]] = [
    {
        "user_id": "user-alice-0001",
        "username": "alice",
        "password": "password123",
        "role": UserRole.USER,
        "display_name": "Alice (Operator)",
    },
    {
        "user_id": "user-bob-0002",
        "username": "bob",
        "password": "password123",
        "role": UserRole.USER,
        "display_name": "Bob (Operator)",
    },
    {
        "user_id": "user-admin-0001",
        "username": "admin",
        "password": "adminpass",
        "role": UserRole.ADMIN,
        "display_name": "Admin (Administrator)",
    },
]

# Build lookup indexes at module load time.
_BY_USERNAME: dict[str, dict[str, str]] = {
    entry["username"]: entry for entry in _PROTOTYPE_CREDENTIALS
}


class PrototypeCredentialStore:
    """Read-only prototype credential store.

    All lookups use constant-time comparison to prevent timing attacks.
    This is a prototype shim — no I/O, no hashing, no external dependencies.
    """

    def lookup(self, username: str) -> UserIdentity | None:
        """Return the :class:`~aegis.auth.models.UserIdentity` for *username*.

        Returns ``None`` if the username is not found.  Never raises.
        """
        entry = _BY_USERNAME.get(username)
        if entry is None:
            return None
        return UserIdentity(
            user_id=entry["user_id"],
            username=entry["username"],
            role=entry["role"],
            display_name=entry["display_name"],
        )

    def verify(self, username: str, password: str) -> UserIdentity | None:
        """Verify *username*/*password* and return identity if correct.

        Uses :func:`secrets.compare_digest` for constant-time comparison.
        Returns ``None`` on any mismatch (unknown user or wrong password) —
        never reveals which condition failed.
        """
        entry = _BY_USERNAME.get(username)
        if entry is None:
            # Still call compare_digest with a dummy value to keep timing uniform.
            secrets.compare_digest("__dummy__", password)
            return None

        if not secrets.compare_digest(entry["password"], password):
            return None

        return UserIdentity(
            user_id=entry["user_id"],
            username=entry["username"],
            role=entry["role"],
            display_name=entry["display_name"],
        )
