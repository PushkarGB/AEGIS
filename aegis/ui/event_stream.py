"""Event streaming infrastructure for the AEGIS Gradio UI.

Provides:
- Human-friendly event labels that hide chain-of-thought.
- SessionEventCollector: per-task sink with identity-scoped filtering.
- Progressive Markdown formatter for incremental UI updates.

Mock-only pacing constant is defined here for use by DeterministicTaskRunner;
real model providers are never given artificial delays.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from uuid import UUID

from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventType,
)

# ---------------------------------------------------------------------------
# Mock-only pacing delay (seconds)
# ---------------------------------------------------------------------------

MOCK_EVENT_PACE_SECONDS: float = 0.35
"""Delay between mock execution steps in DeterministicTaskRunner.

This constant is consumed exclusively by the mock/demo execution path.
Real LocalModelProvider / OllamaProvider execution paths are unaffected.
Tests may override via ``DeterministicTaskRunner(event_pace_seconds=0.0)``.
"""


# ---------------------------------------------------------------------------
# User-friendly event labels (no chain-of-thought)
# ---------------------------------------------------------------------------

_EVENT_LABELS: dict[ExecutionEventType, str] = {
    ExecutionEventType.TASK_STARTED: "Understanding request",
    ExecutionEventType.DOCUMENT_TYPE_IDENTIFIED: "Identifying document type",
    ExecutionEventType.INTENT_IDENTIFIED: "Understanding request",
    ExecutionEventType.WORKFLOW_SELECTED: "Workflow selected",
    ExecutionEventType.CAPABILITY_STARTED: "Processing step",
    ExecutionEventType.CAPABILITY_COMPLETED: "Step completed",
    ExecutionEventType.MODEL_SELECTED: "Preparing model",
    ExecutionEventType.MODEL_INVOKED: "Model processing",
    ExecutionEventType.SANDBOX_STARTED: "Running sandbox",
    ExecutionEventType.SANDBOX_COMPLETED: "Sandbox completed",
    ExecutionEventType.VERIFICATION_STARTED: "Verifying result",
    ExecutionEventType.VERIFICATION_COMPLETED: "Verification complete",
    ExecutionEventType.HITL_REQUIRED: "Awaiting approval",
    ExecutionEventType.TASK_COMPLETED: "Completed",
    ExecutionEventType.TASK_FAILED: "Task failed",
    ExecutionEventType.CAPABILITY_REJECTED: "Action rejected",
    ExecutionEventType.APPROVAL_RECORDED: "Approval recorded",
    ExecutionEventType.LIMIT_EXCEEDED: "Limit exceeded",
}

# Refined labels for specific capability_id values
_CAPABILITY_LABELS: dict[str, str] = {
    "inspect_spreadsheet": "Inspecting workbook",
    "generate_code": "Generating calculation",
    "run_code": "Running sandbox",
    "verify_result": "Verifying result",
    "generate_excel": "Preparing deliverable",
    "generate_word": "Preparing deliverable",
    "generate_ppt": "Preparing deliverable",
    "extract_document": "Extracting document",
    "ocr_document": "Processing OCR",
    "draft_approval_note": "Drafting approval note",
    "analyze_image": "Analyzing image",
    "search_knowledge": "Searching knowledge",
    "finish": "Completing task",
}


def event_label(event: ExecutionEvent) -> str:
    """Return a human-friendly label for an execution event.

    Uses capability-specific labels when available, falling back to
    event-type labels. Never exposes chain-of-thought or model internals.
    """
    # Use capability-specific label for CAPABILITY_STARTED events
    if (
        event.event_type == ExecutionEventType.CAPABILITY_STARTED
        and event.capability_id
        and event.capability_id in _CAPABILITY_LABELS
    ):
        return _CAPABILITY_LABELS[event.capability_id]

    return _EVENT_LABELS.get(event.event_type, "Processing")


# ---------------------------------------------------------------------------
# SessionEventCollector — per-task sink with identity filtering
# ---------------------------------------------------------------------------


class SessionEventCollector:
    """Thread-safe event sink that collects events for a single task execution.

    Subscribes to an ``ExecutionEventPublisher`` and retains only events
    matching the specified ``(session_id, task_id, user_id)`` identity.
    This construction guarantees one user's stream cannot leak to another.

    Usage::

        collector = SessionEventCollector(publisher, session_id, task_id, user_id)
        # ... execution happens, events are published ...
        new_events = collector.drain()  # returns and clears accumulated events
        collector.unsubscribe()
    """

    def __init__(
        self,
        publisher: ExecutionEventPublisher,
        session_id: UUID,
        task_id: UUID,
        user_id: str,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._user_id = user_id
        self._events: list[ExecutionEvent] = []
        self._lock = threading.Lock()
        self._unsubscribe: Callable[[], None] = publisher.subscribe(self._on_event)

    def _on_event(self, event: ExecutionEvent) -> None:
        """Sink callback: retain only events matching our identity scope."""
        if (
            event.session_id == self._session_id
            and event.task_id == self._task_id
            and event.user_id == self._user_id
        ):
            with self._lock:
                self._events.append(event)

    def drain(self) -> list[ExecutionEvent]:
        """Return all accumulated events and clear the internal buffer.

        Thread-safe; can be called concurrently with event publication.
        """
        with self._lock:
            batch = list(self._events)
            self._events.clear()
        return batch

    def all_events(self) -> list[ExecutionEvent]:
        """Return a copy of all accumulated events without clearing."""
        with self._lock:
            return list(self._events)

    def unsubscribe(self) -> None:
        """Remove this collector from the publisher's sink list."""
        self._unsubscribe()


# ---------------------------------------------------------------------------
# Progressive Markdown formatting
# ---------------------------------------------------------------------------


def format_progressive_events(events: list[ExecutionEvent]) -> str:
    """Format a list of execution events into cumulative user-facing Markdown.

    Shows high-level step labels with status indicators. Does not expose
    chain-of-thought, model outputs, or internal reasoning.
    """
    if not events:
        return "*Waiting for execution to begin...*"

    lines: list[str] = []
    seen_labels: set[str] = set()

    for event in events:
        label = event_label(event)

        # Deduplicate sequential identical labels (e.g. multiple CAPABILITY events)
        dedup_key = f"{label}:{event.capability_id or ''}"
        if dedup_key in seen_labels:
            continue
        seen_labels.add(dedup_key)

        status_icon = _status_icon(event)
        lines.append(f"{status_icon} {label}")

    return "\n\n".join(lines)


def _status_icon(event: ExecutionEvent) -> str:
    """Return a clean, professional status indicator for the event."""
    from aegis.events import ExecutionEventStatus

    if event.status == ExecutionEventStatus.COMPLETED:
        return "[DONE]"
    if event.status == ExecutionEventStatus.FAILED:
        return "[FAILED]"
    if event.status == ExecutionEventStatus.REJECTED:
        return "[REJECTED]"
    if event.status == ExecutionEventStatus.REQUIRES_ACTION:
        return "[WAITING]"
    return "[RUNNING]"

