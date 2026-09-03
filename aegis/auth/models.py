"""User identity and authentication data models for the AEGIS prototype.

These are pure, immutable data contracts — no persistence or service logic.
All timestamps must be UTC-aware.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(StrEnum):
    """Prototype roles governing resource access.

    ``USER``  — standard operator with access to own sessions and tasks.
    ``ADMIN`` — platform administrator with access to all sessions and audit.
    """

    USER = "user"
    ADMIN = "admin"


class UserIdentity(BaseModel):
    """Immutable, resolved identity for an authenticated caller.

    Produced by :class:`~aegis.auth.service.AuthService` after successful
    login or token validation.  This record is the canonical representation
    of *who is calling* within a request context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    username: str = Field(min_length=1)
    role: UserRole
    display_name: str = Field(min_length=1)


class AuthToken(BaseModel):
    """An issued authentication token returned to the caller on login.

    ``token`` is an opaque string (UUID4 hex) — it must not encode role or
    identity information directly; callers resolve identity via
    :meth:`~aegis.auth.service.AuthService.resolve_current_user`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    username: str = Field(min_length=1)
    role: UserRole
    issued_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must include timezone information")
        return value


class LoginRequest(BaseModel):
    """Credentials submitted to :meth:`~aegis.auth.service.AuthService.login`."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResult(BaseModel):
    """Result returned from :meth:`~aegis.auth.service.AuthService.login`.

    On success: ``success=True``, ``token`` is populated, ``error`` is ``None``.
    On failure: ``success=False``, ``token`` is ``None``, ``error`` carries a
    human-readable reason safe to display.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    token: AuthToken | None = None
    error: str | None = None
