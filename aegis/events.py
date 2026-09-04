"""Provider-neutral high-level execution event contracts.

Events are intentionally operational: they describe governed runtime progress
without recording prompts, model outputs, or private reasoning.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


JsonObject = dict[str, JsonValue]
ComponentName = Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_.:-]*$")]


class ExecutionEventType(StrEnum):
    """High-level runtime milestones safe for UI and audit consumers."""

    TASK_STARTED = "task_started"
    DOCUMENT_TYPE_IDENTIFIED = "document_type_identified"
    INTENT_IDENTIFIED = "intent_identified"
    WORKFLOW_SELECTED = "workflow_selected"
    CAPABILITY_STARTED = "capability_started"
    CAPABILITY_COMPLETED = "capability_completed"
    MODEL_SELECTED = "model_selected"
    MODEL_INVOKED = "model_invoked"
    SANDBOX_STARTED = "sandbox_started"
    SANDBOX_COMPLETED = "sandbox_completed"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    HITL_REQUIRED = "hitl_required"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"

    # Additional governed outcomes retained for controller diagnostics.
    CAPABILITY_REJECTED = "capability_rejected"
    APPROVAL_RECORDED = "approval_recorded"
    LIMIT_EXCEEDED = "limit_exceeded"


class ExecutionEventStatus(StrEnum):
    """Outcome status of a high-level execution event."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    REQUIRES_ACTION = "requires_action"


class ExecutionEventContext(BaseModel):
    """Identity context required when a producer emits an execution event."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    session_id: UUID
    task_id: UUID
    user_id: str | None = Field(default=None, min_length=1)


class ExecutionEvent(BaseModel):
    """An immutable, JSON-serializable operational event.

    ``metadata`` is limited to safe operational facts such as exit codes,
    routing roles, or retry counts. Producers must never place prompts, model
    completions, hidden reasoning, or chain-of-thought in this field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utc_now)
    session_id: UUID
    task_id: UUID
    event_type: ExecutionEventType
    component: ComponentName
    status: ExecutionEventStatus
    summary: str = Field(min_length=1)
    user_id: str | None = Field(default=None, min_length=1)
    workflow_id: str | None = Field(default=None, min_length=1)
    capability_id: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1)
    model_provider_id: str | None = Field(default=None, min_length=1)
    request_id: UUID | None = None
    metadata: JsonObject = Field(default_factory=dict)
    sequence: int = Field(default=0, ge=0)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone information")
        return value

    @property
    def kind(self) -> ExecutionEventType:
        """Deprecated alias retained for existing Controller consumers."""

        return self.event_type

    @property
    def action(self) -> str | None:
        """Deprecated Controller action alias for ``capability_id``."""

        return self.capability_id

    @property
    def occurred_at(self) -> datetime:
        """Deprecated alias for ``timestamp``."""

        return self.timestamp


ExecutionEventSink = Callable[[ExecutionEvent], None]


class ExecutionEventPublisher:
    """In-memory event stream with a sink boundary for UI and audit adapters.

    A UI can subscribe to receive immutable events as they occur. An audit
    adapter can subscribe and persist ``event.model_dump(mode='json')`` without
    coupling either consumer to Controller, Agent, or a model provider.
    """

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []
        self._sinks: list[ExecutionEventSink] = []

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        """Return the ordered immutable event stream."""

        return tuple(self._events)

    def subscribe(self, sink: ExecutionEventSink) -> Callable[[], None]:
        """Register a UI-streaming or audit sink and return its unsubscribe hook."""

        self._sinks.append(sink)

        def unsubscribe() -> None:
            if sink in self._sinks:
                self._sinks.remove(sink)

        return unsubscribe

    def publish(self, event: ExecutionEvent) -> ExecutionEvent:
        """Assign stream order, retain the event, then synchronously notify sinks."""

        published = event.model_copy(update={"sequence": len(self._events) + 1})
        self._events.append(published)
        for sink in tuple(self._sinks):
            sink(published)
        return published
