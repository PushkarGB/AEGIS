"""Audit logging and event recording service for AEGIS.

Provides:
- AuditService: Central store of ExecutionEvent audit records.
- AuthorizedAuditService: RBAC-enforcing facade requiring VIEW_ALL_AUDIT.
"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Sequence
from uuid import UUID

from aegis.auth.authorization import Permission, require_permission
from aegis.auth.models import UserIdentity
from aegis.events import ExecutionEvent, ExecutionEventType, ExecutionEventStatus


class AuditService:
    """Thread-safe in-memory store for auditable execution events.

    Can be registered as a sink on ``ExecutionEventPublisher`` or invoked
    directly by domain controllers.
    """

    def __init__(self, max_records: int = 10000) -> None:
        self._max_records = max_records
        self._records: list[ExecutionEvent] = []
        self._lock = threading.Lock()

    def record_event(self, event: ExecutionEvent) -> None:
        """Record an execution event in the audit store."""
        with self._lock:
            self._records.append(event)
            if len(self._records) > self._max_records:
                self._records.pop(0)

    # Alias for ExecutionEventSink compatibility
    def __call__(self, event: ExecutionEvent) -> None:
        self.record_event(event)

    def get_records(
        self,
        user_id: str | None = None,
        session_id: UUID | None = None,
        task_id: UUID | None = None,
        event_type: ExecutionEventType | None = None,
        status: ExecutionEventStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[ExecutionEvent]:
        """Return audit records matching filters, newest first."""
        with self._lock:
            filtered = list(self._records)

        if user_id is not None:
            filtered = [r for r in filtered if r.user_id == user_id]
        if session_id is not None:
            filtered = [r for r in filtered if r.session_id == session_id]
        if task_id is not None:
            filtered = [r for r in filtered if r.task_id == task_id]
        if event_type is not None:
            filtered = [r for r in filtered if r.event_type == event_type]
        if status is not None:
            filtered = [r for r in filtered if r.status == status]
        if since is not None:
            filtered = [r for r in filtered if r.timestamp >= since]

        # Newest first
        filtered.sort(key=lambda r: r.timestamp, reverse=True)

        if limit is not None and limit > 0:
            filtered = filtered[:limit]

        return filtered

    def clear(self) -> None:
        """Clear all audit records (primarily for testing)."""
        with self._lock:
            self._records.clear()

    @property
    def total_count(self) -> int:
        """Total count of retained audit records."""
        with self._lock:
            return len(self._records)


class AuthorizedAuditService:
    """Auth-enforcing facade over AuditService.

    Requires VIEW_ALL_AUDIT permission on all operations.
    """

    def __init__(self, inner: AuditService) -> None:
        self._inner = inner

    def get_records(
        self,
        caller: UserIdentity,
        user_id: str | None = None,
        session_id: UUID | None = None,
        task_id: UUID | None = None,
        event_type: ExecutionEventType | None = None,
        status: ExecutionEventStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[ExecutionEvent]:
        """Return audit records after verifying VIEW_ALL_AUDIT."""
        require_permission(caller, Permission.VIEW_ALL_AUDIT)
        return self._inner.get_records(
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            event_type=event_type,
            status=status,
            since=since,
            limit=limit,
        )

    @property
    def inner(self) -> AuditService:
        """Expose inner service for sink registration."""
        return self._inner
