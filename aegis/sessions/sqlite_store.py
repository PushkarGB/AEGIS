"""SQLite-backed session and task repositories.

Uses the Python standard-library ``sqlite3`` module with WAL journal mode for
safe concurrent reads. Timestamps are stored as ISO-8601 UTC strings and
restored as timezone-aware :class:`datetime` objects.

No external dependencies are introduced.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .models import SessionRecord, SessionStatus, TaskRecord, TaskStatus
from .repository import (
    NotFoundError,
    SessionIsolationError,  # noqa: F401 — re-exported for consumers
    SessionRepository,
    TaskRepository,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 UTC string back to a timezone-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_session(row: tuple[Any, ...]) -> SessionRecord:
    session_id, user_id, created_at, updated_at, status = row
    return SessionRecord(
        session_id=UUID(session_id),
        user_id=user_id,
        created_at=_parse_dt(created_at),
        updated_at=_parse_dt(updated_at),
        status=SessionStatus(status),
    )


def _row_to_task(row: tuple[Any, ...]) -> TaskRecord:
    task_id, session_id, user_id, created_at, status, workflow_id = row
    return TaskRecord(
        task_id=UUID(task_id),
        session_id=UUID(session_id),
        user_id=user_id,
        created_at=_parse_dt(created_at),
        status=TaskStatus(status),
        workflow_id=workflow_id if workflow_id else None,
    )


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT    PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL
);
"""

_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT    PRIMARY KEY,
    session_id  TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    workflow_id TEXT
);
"""

_SESSIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_sessions_user_id
    ON sessions (user_id, created_at DESC);
"""

_TASKS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_tasks_session_id
    ON tasks (session_id, created_at DESC);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not already exist."""
    conn.executescript(
        _SESSIONS_DDL + _TASKS_DDL + _SESSIONS_INDEX_DDL + _TASKS_INDEX_DDL
    )
    conn.commit()


# ---------------------------------------------------------------------------
# SQLite session repository
# ---------------------------------------------------------------------------

class SqliteSessionRepository(SessionRepository):
    """Stores session records in an SQLite database.

    Args:
        conn: An already-opened SQLite connection. The caller is responsible
              for closing it. Pass ``sqlite3.connect(":memory:")`` for tests.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    def create_session(
        self,
        user_id: str,
        session_id: UUID | None = None,
    ) -> SessionRecord:
        now = _utc_now_str()
        sid = session_id if session_id is not None else uuid4()
        self._conn.execute(
            "INSERT INTO sessions (session_id, user_id, created_at, updated_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(sid), user_id, now, now, SessionStatus.ACTIVE.value),
        )
        self._conn.commit()
        return self.get_session(sid)

    # ------------------------------------------------------------------
    def get_session(self, session_id: UUID) -> SessionRecord:
        cur = self._conn.execute(
            "SELECT session_id, user_id, created_at, updated_at, status "
            "FROM sessions WHERE session_id = ?",
            (str(session_id),),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"Session {session_id!s} not found")
        return _row_to_session(row)

    # ------------------------------------------------------------------
    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        cur = self._conn.execute(
            "SELECT session_id, user_id, created_at, updated_at, status "
            "FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [_row_to_session(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    def update_session_status(
        self,
        session_id: UUID,
        status: SessionStatus,
    ) -> SessionRecord:
        now = _utc_now_str()
        cur = self._conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status.value, now, str(session_id)),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"Session {session_id!s} not found")
        return self.get_session(session_id)


# ---------------------------------------------------------------------------
# SQLite task repository
# ---------------------------------------------------------------------------

class SqliteTaskRepository(TaskRepository):
    """Stores task records in an SQLite database.

    Args:
        conn: An already-opened SQLite connection. May be shared with
              :class:`SqliteSessionRepository` for atomic operations.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    def create_task(
        self,
        session_id: UUID,
        user_id: str,
        workflow_id: str | None = None,
        task_id: UUID | None = None,
    ) -> TaskRecord:
        now = _utc_now_str()
        tid = task_id if task_id is not None else uuid4()
        self._conn.execute(
            "INSERT INTO tasks (task_id, session_id, user_id, created_at, status, workflow_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(tid),
                str(session_id),
                user_id,
                now,
                TaskStatus.PENDING.value,
                workflow_id,
            ),
        )
        self._conn.commit()
        return self.get_task(tid)

    # ------------------------------------------------------------------
    def get_task(self, task_id: UUID) -> TaskRecord:
        cur = self._conn.execute(
            "SELECT task_id, session_id, user_id, created_at, status, workflow_id "
            "FROM tasks WHERE task_id = ?",
            (str(task_id),),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(f"Task {task_id!s} not found")
        return _row_to_task(row)

    # ------------------------------------------------------------------
    def update_task_status(
        self,
        task_id: UUID,
        status: TaskStatus,
    ) -> TaskRecord:
        cur = self._conn.execute(
            "UPDATE tasks SET status = ? WHERE task_id = ?",
            (status.value, str(task_id)),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"Task {task_id!s} not found")
        return self.get_task(task_id)

    # ------------------------------------------------------------------
    def get_tasks_for_session(
        self,
        session_id: UUID,
        user_id: str,
    ) -> list[TaskRecord]:
        cur = self._conn.execute(
            "SELECT task_id, session_id, user_id, created_at, status, workflow_id "
            "FROM tasks WHERE session_id = ? AND user_id = ? "
            "ORDER BY created_at DESC",
            (str(session_id), user_id),
        )
        return [_row_to_task(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class SqliteStoreFactory:
    """Creates an initialised pair of SQLite repositories sharing one connection.

    Args:
        db_path: Filesystem path to the SQLite database file, or ``":memory:"``
                 for an in-process in-memory database (primarily for tests).
    """

    @staticmethod
    def create(
        db_path: str | Path = ":memory:",
    ) -> tuple[SqliteSessionRepository, SqliteTaskRepository]:
        """Open (or create) the database, initialise the schema, and return repos.

        The returned repositories share the same connection so that reads
        during a transaction are consistent. WAL mode is enabled for safer
        concurrent read access when a file path is used.
        """
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(conn)
        session_repo = SqliteSessionRepository(conn)
        task_repo = SqliteTaskRepository(conn)
        return session_repo, task_repo
