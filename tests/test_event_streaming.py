"""Integration tests for execution event streaming to the UI.

Validates:
- Progressive event streaming with human-friendly labels.
- Identity preservation (session_id, task_id, user_id) on every event.
- Per-user event isolation via SessionEventCollector.
- No chain-of-thought exposure in event labels.
- Approval workflow event streaming.
- SessionEventCollector drain/clear semantics.
- Generator-based submit_task_streaming yields incremental updates.

All tests use MockModelProvider; no Ollama required.
"""

from __future__ import annotations

import threading
from uuid import UUID, uuid4

import pytest

from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration.hitl import HITLApprovalState
from aegis.schemas import FinalStatus
from aegis.sessions import TaskStatus
from aegis.ui.event_stream import (
    MOCK_EVENT_PACE_SECONDS,
    SessionEventCollector,
    event_label,
    format_progressive_events,
)
from aegis.ui.service import UIBackendService, UIStreamUpdate, UITaskResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ui_service() -> UIBackendService:
    """Provide a fresh UIBackendService with in-memory storage."""
    return UIBackendService(db_path=":memory:")


@pytest.fixture
def user_token(ui_service: UIBackendService) -> str:
    """Log in alice and return her token."""
    _, _, _, token = ui_service.login("alice", "password123")
    assert token is not None
    return token


@pytest.fixture
def bob_token(ui_service: UIBackendService) -> str:
    """Log in bob and return his token."""
    _, _, _, token = ui_service.login("bob", "password123")
    assert token is not None
    return token


@pytest.fixture
def alice_session(ui_service: UIBackendService, user_token: str) -> UUID:
    """Create a session for alice and return its ID."""
    sess = ui_service.create_session(user_token)
    return sess.session_id


@pytest.fixture
def bob_session(ui_service: UIBackendService, bob_token: str) -> UUID:
    """Create a session for bob and return its ID."""
    sess = ui_service.create_session(bob_token)
    return sess.session_id


# ---------------------------------------------------------------------------
# 1. Progressive event streaming (computation workflow)
# ---------------------------------------------------------------------------


def test_progressive_events_computation(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """Computation task produces progressive events with human-friendly labels."""
    result = ui_service.submit_task(
        token_str=user_token,
        session_id=alice_session,
        prompt="Calculate the average measured thickness from equipment readings.",
        attachment_path="inspection.xlsx",
    )

    assert result.final_status == FinalStatus.COMPLETED
    assert len(result.events) > 0

    # Verify events have human-friendly labels (not raw enum names)
    labels = [event_label(ev) for ev in result.events]
    assert "Understanding request" in labels
    assert "Workflow selected" in labels

    # At least some capability-specific labels should appear
    all_labels = set(labels)
    expected_any = {
        "Inspecting workbook",
        "Generating calculation",
        "Running sandbox",
        "Verifying result",
        "Preparing deliverable",
    }
    assert all_labels & expected_any, f"Expected some of {expected_any} in {all_labels}"


# ---------------------------------------------------------------------------
# 2. Identity preservation
# ---------------------------------------------------------------------------


def test_progressive_events_preserve_identity(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """Every event carries correct session_id, task_id, and user_id."""
    result = ui_service.submit_task(
        token_str=user_token,
        session_id=alice_session,
        prompt="Calculate the average measured thickness.",
        attachment_path="readings.xlsx",
    )

    for event in result.events:
        assert event.session_id == alice_session, "session_id mismatch"
        assert event.task_id == result.task_id, "task_id mismatch"
        assert event.user_id == "user-alice-0001", "user_id mismatch"


# ---------------------------------------------------------------------------
# 3. Event isolation between users
# ---------------------------------------------------------------------------


def test_event_isolation_between_users(
    ui_service: UIBackendService,
    user_token: str,
    bob_token: str,
    alice_session: UUID,
    bob_session: UUID,
):
    """Two users submit tasks; SessionEventCollector isolates each user's events."""
    publisher = ui_service.event_publisher

    # Set up collectors BEFORE task submission
    alice_task_id = uuid4()
    bob_task_id = uuid4()

    # Submit alice's task
    alice_result = ui_service.submit_task(
        token_str=user_token,
        session_id=alice_session,
        prompt="Calculate average thickness.",
        attachment_path="data.xlsx",
    )

    # Set up collectors for each user's task using their actual task IDs
    alice_collector = SessionEventCollector(
        publisher, alice_session, alice_result.task_id, "user-alice-0001"
    )
    bob_collector_for_alice = SessionEventCollector(
        publisher, alice_session, alice_result.task_id, "user-bob-0002"
    )

    # Submit bob's task
    bob_result = ui_service.submit_task(
        token_str=bob_token,
        session_id=bob_session,
        prompt="Compute pressure readings.",
        attachment_path="pressure.xlsx",
    )

    bob_collector = SessionEventCollector(
        publisher, bob_session, bob_result.task_id, "user-bob-0002"
    )
    alice_collector_for_bob = SessionEventCollector(
        publisher, bob_session, bob_result.task_id, "user-alice-0001"
    )

    # Publish a test event for alice's task
    publisher.publish(
        ExecutionEvent(
            session_id=alice_session,
            task_id=alice_result.task_id,
            user_id="user-alice-0001",
            event_type=ExecutionEventType.TASK_COMPLETED,
            component="test",
            status=ExecutionEventStatus.COMPLETED,
            summary="Alice post-test event.",
        )
    )

    # Publish a test event for bob's task
    publisher.publish(
        ExecutionEvent(
            session_id=bob_session,
            task_id=bob_result.task_id,
            user_id="user-bob-0002",
            event_type=ExecutionEventType.TASK_COMPLETED,
            component="test",
            status=ExecutionEventStatus.COMPLETED,
            summary="Bob post-test event.",
        )
    )

    # Alice collector gets alice's event, not bob's
    alice_events = alice_collector.drain()
    assert len(alice_events) == 1
    assert alice_events[0].user_id == "user-alice-0001"

    # Bob collector scoped to alice's task gets nothing (wrong user_id)
    bob_for_alice_events = bob_collector_for_alice.drain()
    assert len(bob_for_alice_events) == 0, "Bob's collector should not see alice's events"

    # Bob collector gets bob's event, not alice's
    bob_events = bob_collector.drain()
    assert len(bob_events) == 1
    assert bob_events[0].user_id == "user-bob-0002"

    # Alice collector scoped to bob's task gets nothing (wrong user_id)
    alice_for_bob_events = alice_collector_for_bob.drain()
    assert len(alice_for_bob_events) == 0, "Alice's collector should not see bob's events"

    # Verify task IDs are distinct
    assert alice_result.task_id != bob_result.task_id

    # Clean up
    alice_collector.unsubscribe()
    bob_collector_for_alice.unsubscribe()
    bob_collector.unsubscribe()
    alice_collector_for_bob.unsubscribe()


def test_collector_isolates_by_identity():
    """SessionEventCollector only retains events matching its identity scope."""
    publisher = ExecutionEventPublisher()

    session_a = uuid4()
    session_b = uuid4()
    task_a = uuid4()
    task_b = uuid4()

    collector_a = SessionEventCollector(publisher, session_a, task_a, "user-alice")
    collector_b = SessionEventCollector(publisher, session_b, task_b, "user-bob")

    # Publish event for alice
    publisher.publish(
        ExecutionEvent(
            session_id=session_a,
            task_id=task_a,
            user_id="user-alice",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Alice task started.",
        )
    )

    # Publish event for bob
    publisher.publish(
        ExecutionEvent(
            session_id=session_b,
            task_id=task_b,
            user_id="user-bob",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Bob task started.",
        )
    )

    alice_events = collector_a.drain()
    bob_events = collector_b.drain()

    assert len(alice_events) == 1
    assert alice_events[0].user_id == "user-alice"
    assert len(bob_events) == 1
    assert bob_events[0].user_id == "user-bob"

    collector_a.unsubscribe()
    collector_b.unsubscribe()


# ---------------------------------------------------------------------------
# 4. No chain-of-thought in event labels
# ---------------------------------------------------------------------------


def test_event_labels_no_chain_of_thought(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """Event labels contain only high-level descriptions, no model internals."""
    result = ui_service.submit_task(
        token_str=user_token,
        session_id=alice_session,
        prompt="Calculate the average measured thickness.",
        attachment_path="readings.xlsx",
    )

    forbidden_fragments = [
        "chain-of-thought",
        "thinking",
        "model output",
        "prompt",
        "system_prompt",
        "model_response",
        "raw_text",
    ]

    for event in result.events:
        label = event_label(event)
        label_lower = label.lower()
        for forbidden in forbidden_fragments:
            assert forbidden not in label_lower, (
                f"Event label '{label}' contains forbidden fragment '{forbidden}'"
            )


# ---------------------------------------------------------------------------
# 5. Approval workflow streaming
# ---------------------------------------------------------------------------


def test_approval_workflow_streaming(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """Approval workflow produces HITL_REQUIRED and AWAITING_APPROVAL events."""
    result = ui_service.submit_task(
        token_str=user_token,
        session_id=alice_session,
        prompt="Prepare approval note from scanned inspection report.",
        attachment_path="report.pdf",
    )

    assert result.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL

    event_types = [ev.event_type for ev in result.events]
    assert ExecutionEventType.HITL_REQUIRED in event_types

    # Check that "Awaiting approval" label appears
    labels = [event_label(ev) for ev in result.events]
    assert "Awaiting approval" in labels

    # Verify identity is preserved through HITL events
    for event in result.events:
        assert event.session_id == alice_session
        assert event.task_id == result.task_id
        assert event.user_id == "user-alice-0001"


# ---------------------------------------------------------------------------
# 6. Collector drain semantics
# ---------------------------------------------------------------------------


def test_collector_drain_clears():
    """drain() returns events exactly once and clears the buffer."""
    publisher = ExecutionEventPublisher()
    session_id = uuid4()
    task_id = uuid4()

    collector = SessionEventCollector(publisher, session_id, task_id, "user-test")

    publisher.publish(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="user-test",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Test event.",
        )
    )

    first_drain = collector.drain()
    assert len(first_drain) == 1

    second_drain = collector.drain()
    assert len(second_drain) == 0, "drain() should clear accumulated events"

    collector.unsubscribe()


def test_collector_all_events_does_not_clear():
    """all_events() returns a copy without clearing."""
    publisher = ExecutionEventPublisher()
    session_id = uuid4()
    task_id = uuid4()

    collector = SessionEventCollector(publisher, session_id, task_id, "user-test")

    publisher.publish(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="user-test",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Test event.",
        )
    )

    first = collector.all_events()
    second = collector.all_events()
    assert len(first) == 1
    assert len(second) == 1, "all_events() should not clear"

    collector.unsubscribe()


# ---------------------------------------------------------------------------
# 7. Generator-based streaming
# ---------------------------------------------------------------------------


def test_streaming_submit_task_yields(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """submit_task_streaming() yields incremental UIStreamUpdate objects."""
    updates: list[UIStreamUpdate] = []

    for update in ui_service.submit_task_streaming(
        token_str=user_token,
        session_id=alice_session,
        prompt="Calculate average thickness from readings.",
        attachment_path="data.xlsx",
        poll_interval=0.05,  # Fast polling for test speed
    ):
        updates.append(update)

    # Must have at least one update
    assert len(updates) >= 1, "Expected at least one streaming update"

    # Last update must be final
    final = updates[-1]
    assert final.is_final is True
    assert final.result is not None
    assert final.result.final_status == FinalStatus.COMPLETED

    # Events markdown should be non-empty
    assert final.events_markdown
    assert "Understanding request" in final.events_markdown or "Workflow selected" in final.events_markdown

    # All intermediate updates should not be final
    for upd in updates[:-1]:
        assert upd.is_final is False
        assert upd.events_markdown  # Non-empty


def test_streaming_approval_workflow_yields_hitl(
    ui_service: UIBackendService, user_token: str, alice_session: UUID
):
    """Streaming approval workflow yields events and pauses at HITL."""
    updates: list[UIStreamUpdate] = []

    for update in ui_service.submit_task_streaming(
        token_str=user_token,
        session_id=alice_session,
        prompt="Review inspection report for approval.",
        attachment_path="report.pdf",
        poll_interval=0.05,
    ):
        updates.append(update)

    final = updates[-1]
    assert final.is_final is True
    assert final.result is not None
    assert final.result.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL
    assert "Awaiting approval" in final.events_markdown


# ---------------------------------------------------------------------------
# 8. format_progressive_events output
# ---------------------------------------------------------------------------


def test_format_progressive_events_empty():
    """Empty event list produces waiting message."""
    result = format_progressive_events([])
    assert "Waiting" in result


def test_format_progressive_events_deduplicates():
    """Duplicate labels for the same capability are deduplicated."""
    session_id = uuid4()
    task_id = uuid4()

    events = [
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="user-test",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Started.",
        ),
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="user-test",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Started again.",
        ),
    ]

    md = format_progressive_events(events)
    # "Understanding request" should appear only once despite two TASK_STARTED events
    assert md.count("Understanding request") == 1
