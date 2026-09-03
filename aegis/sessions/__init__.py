"""Session and task persistence for the AEGIS prototype (SQLite-backed)."""

from .authorized_service import AuthorizedSessionService
from .models import SessionRecord, SessionStatus, TaskRecord, TaskStatus
from .repository import (
    NotFoundError,
    SessionIsolationError,
    SessionRepository,
    TaskRepository,
)
from .service import SessionService
from .sqlite_store import (
    SqliteSessionRepository,
    SqliteStoreFactory,
    SqliteTaskRepository,
)

__all__ = [
    # Models
    "SessionRecord",
    "SessionStatus",
    "TaskRecord",
    "TaskStatus",
    # Exceptions
    "NotFoundError",
    "SessionIsolationError",
    # Abstract repositories
    "SessionRepository",
    "TaskRepository",
    # SQLite implementations
    "SqliteSessionRepository",
    "SqliteTaskRepository",
    "SqliteStoreFactory",
    # Service
    "SessionService",
    "AuthorizedSessionService",
]
