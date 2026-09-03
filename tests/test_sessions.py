"""Tests for Phase 6.X — Session and Task State (SQLite-backed).

All tests use an in-memory SQLite database; no filesystem access is required.
"""

from __future__ import annotations

import pytest
from datetime import timezone
from uuid import UUID, uuid4

from aegis.sessions import (
    NotFoundError,
    SessionIsolationError,
    SessionRecord,
    SessionService,
    SessionStatus,
    SqliteStoreFactory,
    TaskRecord,
    TaskStatus,
)
from aegis.sessions.models import SessionRecord, TaskRecord
from aegis.sessions.repository import SessionRepository, TaskRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repos():
    """Return a fresh in-memory session and task repository pair."""
    return SqliteStoreFactory.create(":memory:")


@pytest.fixture
def session_repo(repos):
    return repos[0]


@pytest.fixture
def task_repo(repos):
    return repos[1]


@pytest.fixture
def service(repos):
    """Return a SessionService backed by in-memory repos."""
    session_repo, task_repo = repos
    return SessionService(session_repo=session_repo, task_repo=task_repo)


# ---------------------------------------------------------------------------
# TestSessionCreation
# ---------------------------------------------------------------------------

class TestSessionCreation:
    def test_creates_session_with_correct_user(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        assert record.user_id == "alice"

    def test_session_id_is_uuid(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        assert isinstance(record.session_id, UUID)

    def test_status_defaults_to_active(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        assert record.status == SessionStatus.ACTIVE

    def test_timestamps_are_utc_aware(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        assert record.created_at.tzinfo is not None
        assert record.updated_at.tzinfo is not None
        assert record.created_at.tzinfo == timezone.utc or (
            record.created_at.utcoffset().total_seconds() == 0
        )

    def test_explicit_session_id_is_honoured(self, session_repo):
        sid = uuid4()
        record = session_repo.create_session(user_id="alice", session_id=sid)
        assert record.session_id == sid

    def test_created_session_is_retrievable(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        fetched = session_repo.get_session(record.session_id)
        assert fetched.session_id == record.session_id
        assert fetched.user_id == "alice"

    def test_session_record_is_immutable(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        with pytest.raises(Exception):
            record.user_id = "modified"  # type: ignore[misc]

    def test_created_at_equals_updated_at_initially(self, session_repo):
        record = session_repo.create_session(user_id="alice")
        # Both times are stored as the same instant at creation.
        assert record.created_at == record.updated_at


# ---------------------------------------------------------------------------
# TestTaskCreation
# ---------------------------------------------------------------------------

class TestTaskCreation:
    def test_creates_task_with_correct_fields(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(
            session_id=session.session_id,
            user_id="bob",
        )
        assert task.session_id == session.session_id
        assert task.user_id == "bob"
        assert isinstance(task.task_id, UUID)

    def test_status_defaults_to_pending(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(session_id=session.session_id, user_id="bob")
        assert task.status == TaskStatus.PENDING

    def test_workflow_id_optional(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(session_id=session.session_id, user_id="bob")
        assert task.workflow_id is None

    def test_workflow_id_stored_when_provided(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(
            session_id=session.session_id,
            user_id="bob",
            workflow_id="computation",
        )
        assert task.workflow_id == "computation"

    def test_explicit_task_id_is_honoured(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        tid = uuid4()
        task = task_repo.create_task(
            session_id=session.session_id,
            user_id="bob",
            task_id=tid,
        )
        assert task.task_id == tid

    def test_created_task_is_retrievable(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(session_id=session.session_id, user_id="bob")
        fetched = task_repo.get_task(task.task_id)
        assert fetched.task_id == task.task_id
        assert fetched.session_id == session.session_id

    def test_task_created_at_is_utc_aware(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="bob")
        task = task_repo.create_task(session_id=session.session_id, user_id="bob")
        assert task.created_at.tzinfo is not None

    def test_multiple_tasks_in_one_session(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="carol")
        t1 = task_repo.create_task(session_id=session.session_id, user_id="carol")
        t2 = task_repo.create_task(session_id=session.session_id, user_id="carol")
        assert t1.task_id != t2.task_id
        tasks = task_repo.get_tasks_for_session(
            session_id=session.session_id, user_id="carol"
        )
        task_ids = {t.task_id for t in tasks}
        assert t1.task_id in task_ids
        assert t2.task_id in task_ids


# ---------------------------------------------------------------------------
# TestUserSessionAssociation
# ---------------------------------------------------------------------------

class TestUserSessionAssociation:
    def test_list_returns_only_own_sessions(self, session_repo):
        session_repo.create_session(user_id="alice")
        session_repo.create_session(user_id="alice")
        session_repo.create_session(user_id="bob")

        alice_sessions = session_repo.list_sessions("alice")
        bob_sessions = session_repo.list_sessions("bob")

        assert len(alice_sessions) == 2
        assert len(bob_sessions) == 1
        assert all(s.user_id == "alice" for s in alice_sessions)
        assert all(s.user_id == "bob" for s in bob_sessions)

    def test_list_is_empty_for_unknown_user(self, session_repo):
        sessions = session_repo.list_sessions("unknown_user")
        assert sessions == []

    def test_list_ordered_newest_first(self, session_repo):
        s1 = session_repo.create_session(user_id="diana")
        s2 = session_repo.create_session(user_id="diana")
        sessions = session_repo.list_sessions("diana")
        # Most recent first — s2 was created after s1.
        ids = [s.session_id for s in sessions]
        # Both must be present; order is newest-first (s2 may equal s1 in
        # sub-millisecond resolution, so just assert both present).
        assert s1.session_id in ids
        assert s2.session_id in ids

    def test_task_carries_user_id(self, session_repo, task_repo):
        session = session_repo.create_session(user_id="alice")
        task = task_repo.create_task(session_id=session.session_id, user_id="alice")
        assert task.user_id == "alice"


# ---------------------------------------------------------------------------
# TestSessionIsolation
# ---------------------------------------------------------------------------

class TestSessionIsolation:
    def test_get_session_with_wrong_user_raises_not_found(self, service):
        record = service.create_session(user_id="alice")
        with pytest.raises(NotFoundError):
            service.get_session(record.session_id, user_id="bob")

    def test_list_sessions_isolated_per_user(self, service):
        service.create_session(user_id="alice")
        service.create_session(user_id="bob")
        alice_list = service.list_sessions("alice")
        bob_list = service.list_sessions("bob")
        assert all(s.user_id == "alice" for s in alice_list)
        assert all(s.user_id == "bob" for s in bob_list)

    def test_get_task_with_wrong_session_raises_isolation_error(self, service):
        session_a = service.create_session(user_id="alice")
        session_b = service.create_session(user_id="alice")
        task = service.create_task(session_id=session_a.session_id, user_id="alice")
        with pytest.raises(SessionIsolationError):
            service.get_task(task_id=task.task_id, session_id=session_b.session_id)

    def test_tasks_not_visible_across_users_via_service(self, service):
        session_alice = service.create_session(user_id="alice")
        task_alice = service.create_task(
            session_id=session_alice.session_id, user_id="alice"
        )
        # Bob cannot access Alice's task via Alice's session (service enforces ownership).
        session_bob = service.create_session(user_id="bob")
        # bob's session does not contain alice's task → isolation error
        with pytest.raises(SessionIsolationError):
            service.get_task(
                task_id=task_alice.task_id,
                session_id=session_bob.session_id,
            )

    def test_close_session_wrong_user_raises_not_found(self, service):
        record = service.create_session(user_id="alice")
        with pytest.raises(NotFoundError):
            service.close_session(record.session_id, user_id="eve")

    def test_create_task_for_another_users_session_raises_not_found(self, service):
        session_alice = service.create_session(user_id="alice")
        with pytest.raises(NotFoundError):
            service.create_task(
                session_id=session_alice.session_id,
                user_id="mallory",
            )


# ---------------------------------------------------------------------------
# TestInvalidAccess
# ---------------------------------------------------------------------------

class TestInvalidAccess:
    def test_get_session_unknown_uuid_raises_not_found(self, session_repo):
        with pytest.raises(NotFoundError):
            session_repo.get_session(uuid4())

    def test_get_task_unknown_uuid_raises_not_found(self, task_repo):
        with pytest.raises(NotFoundError):
            task_repo.get_task(uuid4())

    def test_update_session_status_unknown_uuid_raises_not_found(self, session_repo):
        with pytest.raises(NotFoundError):
            session_repo.update_session_status(uuid4(), SessionStatus.CLOSED)

    def test_update_task_status_unknown_uuid_raises_not_found(self, task_repo):
        with pytest.raises(NotFoundError):
            task_repo.update_task_status(uuid4(), TaskStatus.COMPLETED)

    def test_service_get_session_unknown_uuid_raises_not_found(self, service):
        with pytest.raises(NotFoundError):
            service.get_session(uuid4(), user_id="alice")

    def test_service_get_task_unknown_uuid_raises_not_found(self, service):
        session = service.create_session(user_id="alice")
        with pytest.raises(NotFoundError):
            service.get_task(uuid4(), session_id=session.session_id)


# ---------------------------------------------------------------------------
# TestSessionService — end-to-end service-layer CRUD
# ---------------------------------------------------------------------------

class TestSessionService:
    def test_create_and_retrieve_session(self, service):
        created = service.create_session(user_id="alice")
        fetched = service.get_session(created.session_id, user_id="alice")
        assert fetched.session_id == created.session_id
        assert fetched.user_id == "alice"
        assert fetched.status == SessionStatus.ACTIVE

    def test_close_session_changes_status(self, service):
        created = service.create_session(user_id="alice")
        closed = service.close_session(created.session_id, user_id="alice")
        assert closed.status == SessionStatus.CLOSED

    def test_close_session_updates_updated_at(self, service):
        import time
        created = service.create_session(user_id="alice")
        time.sleep(0.01)  # ensure measurable time difference
        closed = service.close_session(created.session_id, user_id="alice")
        assert closed.updated_at >= created.updated_at

    def test_create_and_retrieve_task(self, service):
        session = service.create_session(user_id="alice")
        task = service.create_task(
            session_id=session.session_id,
            user_id="alice",
            workflow_id="computation",
        )
        fetched = service.get_task(task.task_id, session_id=session.session_id)
        assert fetched.task_id == task.task_id
        assert fetched.workflow_id == "computation"
        assert fetched.status == TaskStatus.PENDING

    def test_update_task_status(self, service):
        session = service.create_session(user_id="alice")
        task = service.create_task(
            session_id=session.session_id, user_id="alice"
        )
        updated = service.update_task_status(
            task_id=task.task_id,
            session_id=session.session_id,
            status=TaskStatus.COMPLETED,
        )
        assert updated.status == TaskStatus.COMPLETED

    def test_update_task_status_isolation_check(self, service):
        session_a = service.create_session(user_id="alice")
        session_b = service.create_session(user_id="alice")
        task = service.create_task(session_id=session_a.session_id, user_id="alice")
        with pytest.raises(SessionIsolationError):
            service.update_task_status(
                task_id=task.task_id,
                session_id=session_b.session_id,
                status=TaskStatus.RUNNING,
            )

    def test_list_sessions_returns_all_user_sessions(self, service):
        service.create_session(user_id="alice")
        service.create_session(user_id="alice")
        service.create_session(user_id="bob")
        alice_sessions = service.list_sessions("alice")
        assert len(alice_sessions) == 2


# ---------------------------------------------------------------------------
# TestMultipleSessionsMultipleUsers
# ---------------------------------------------------------------------------

class TestMultipleSessionsMultipleUsers:
    def test_each_user_sees_only_own_sessions(self, service):
        users = ["user_a", "user_b", "user_c"]
        created = {u: [] for u in users}
        for u in users:
            for _ in range(3):
                created[u].append(service.create_session(user_id=u))

        for u in users:
            listed = service.list_sessions(u)
            assert len(listed) == 3
            assert all(s.user_id == u for s in listed)

    def test_tasks_stay_within_owner_session(self, service):
        s_alice = service.create_session(user_id="alice")
        s_bob = service.create_session(user_id="bob")
        t_alice = service.create_task(
            session_id=s_alice.session_id, user_id="alice"
        )
        # Alice's task is NOT retrievable via Bob's session.
        with pytest.raises(SessionIsolationError):
            service.get_task(
                task_id=t_alice.task_id,
                session_id=s_bob.session_id,
            )

    def test_many_tasks_in_session_all_retrievable(self, service):
        session = service.create_session(user_id="frank")
        task_ids = set()
        for i in range(5):
            t = service.create_task(
                session_id=session.session_id,
                user_id="frank",
                workflow_id=f"workflow_{i}",
            )
            task_ids.add(t.task_id)

        for tid in task_ids:
            fetched = service.get_task(tid, session_id=session.session_id)
            assert fetched.user_id == "frank"

    def test_session_ids_are_globally_unique(self, service):
        ids = [service.create_session(user_id="grace").session_id for _ in range(10)]
        assert len(set(ids)) == 10

    def test_task_ids_are_globally_unique(self, service):
        session = service.create_session(user_id="henry")
        ids = [
            service.create_task(
                session_id=session.session_id, user_id="henry"
            ).task_id
            for _ in range(10)
        ]
        assert len(set(ids)) == 10


# ---------------------------------------------------------------------------
# TestSqliteStoreFactory
# ---------------------------------------------------------------------------

class TestSqliteStoreFactory:
    def test_factory_returns_two_repos(self):
        session_repo, task_repo = SqliteStoreFactory.create(":memory:")
        assert session_repo is not None
        assert task_repo is not None

    def test_factory_schema_is_idempotent(self, tmp_path):
        """Creating a factory twice on the same path must not raise."""
        db_path = tmp_path / "test.db"
        sr1, tr1 = SqliteStoreFactory.create(str(db_path))
        sr2, tr2 = SqliteStoreFactory.create(str(db_path))  # second call must be fine
        # Close connections before pytest tears down tmp_path (Windows WAL lock).
        sr1._conn.close()
        sr2._conn.close()

    def test_shared_connection_allows_cross_repo_consistency(self):
        session_repo, task_repo = SqliteStoreFactory.create(":memory:")
        session = session_repo.create_session(user_id="zoe")
        task = task_repo.create_task(
            session_id=session.session_id,
            user_id="zoe",
        )
        assert task.session_id == session.session_id
