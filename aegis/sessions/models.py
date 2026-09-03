"""Provider-neutral session and task record models.

These are pure data contracts — no persistence logic. The Controller and Agent
operate on ``TaskState``; these records track the user-visible lifecycle of a
session (a user interaction context) and a task (one governed execution within
that session).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStatus(StrEnum):
    """Lifecycle status of a user session."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSED = "closed"


class TaskStatus(StrEnum):
    """Lifecycle status of a governed task within a session."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionRecord(BaseModel):
    """An immutable view of a persisted session.

    A session is a user interaction context. It may contain multiple tasks.
    All timestamps must be UTC-aware.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    status: SessionStatus = SessionStatus.ACTIVE

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must include timezone information")
        return value


class TaskRecord(BaseModel):
    """An immutable view of a persisted task.

    A task is one governed agentic execution within a session. The
    ``workflow_id`` mirrors the ``selected_skill`` field of ``TaskState``
    and may be ``None`` when not yet determined.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    user_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utc_now)
    status: TaskStatus = TaskStatus.PENDING
    workflow_id: str | None = Field(default=None, min_length=1)

    @field_validator("created_at")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include timezone information")
        return value
