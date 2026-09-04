"""Tests for PersistentAuditService, AuditLogReader, and cascading audit capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.audit import AuditLogReader, PersistentAuditService
from aegis.broker import CapabilityBroker
from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration import ExecutionController, WorkflowName
from aegis.schemas import AgentDecision, CapabilityResult, CapabilityResultStatus, TaskState
from aegis.ui.service import UIBackendService


def test_persistent_audit_service_appends_and_reloads(tmp_path: Path):
    log_file = tmp_path / "events.jsonl"
    service = PersistentAuditService(log_path=log_file)

    session_id = uuid4()
    task_id = uuid4()
    user_id = "alice"

    event = ExecutionEvent(
        session_id=session_id,
        task_id=task_id,
        user_id=user_id,
        event_type=ExecutionEventType.TASK_STARTED,
        component="execution_controller",
        status=ExecutionEventStatus.STARTED,
        summary="Task initiated",
        metadata={"custom_info": "test1"},
    )

    service.record_event(event)

    # Check file on disk
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["user_id"] == "alice"
    assert data["metadata"]["custom_info"] == "test1"

    # Verify reloading from disk
    service2 = PersistentAuditService(log_path=log_file)
    records = service2.get_records(user_id="alice")
    assert len(records) == 1
    assert records[0].summary == "Task initiated"


def test_audit_log_reader_hierarchical_grouping(tmp_path: Path):
    log_file = tmp_path / "events.jsonl"
    service = PersistentAuditService(log_path=log_file)

    session_1 = uuid4()
    task_1 = uuid4()
    task_2 = uuid4()
    session_2 = uuid4()
    task_3 = uuid4()

    # Alice: session 1, task 1 & task 2
    for tid in (task_1, task_2):
        service.record_event(
            ExecutionEvent(
                session_id=session_1,
                task_id=tid,
                user_id="alice",
                event_type=ExecutionEventType.TASK_STARTED,
                component="test",
                status=ExecutionEventStatus.STARTED,
                summary=f"Alice task {tid}",
            )
        )

    # Bob: session 2, task 3
    service.record_event(
        ExecutionEvent(
            session_id=session_2,
            task_id=task_3,
            user_id="bob",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Bob task",
        )
    )

    reader = AuditLogReader(log_path=log_file)
    users = reader.get_users()
    assert users == ["alice", "bob"]

    alice_sessions = reader.get_sessions("alice")
    assert alice_sessions == [str(session_1)]

    alice_tasks = reader.get_tasks("alice", str(session_1))
    assert set(alice_tasks) == {str(task_1), str(task_2)}

    events = reader.get_request_events("alice", str(session_1), str(task_1))
    assert len(events) == 1
    assert events[0]["user_id"] == "alice"


def test_execution_controller_metadata_enrichment():
    session_id = uuid4()
    task_id = uuid4()
    publisher = ExecutionEventPublisher()

    class MockBroker(CapabilityBroker):
        def invoke(self, request):
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.SUCCEEDED,
                output={"rows": 10},
            )

    broker = MockBroker()
    state = TaskState(
        task_id=task_id,
        session_id=session_id,
        user_id="charlie",
        user_goal="Calculate equipment wear",
    )
    controller = ExecutionController(
        state=state,
        workflow=WorkflowName.COMPUTATION,
        broker=broker,
        event_publisher=publisher,
    )

    decision = AgentDecision(
        action="inspect_spreadsheet",
        inputs={"file": "test.xlsx"},
        done=False,
    )
    controller.execute(decision)

    # Find CAPABILITY_STARTED event
    started_events = [
        ev for ev in controller.execution_events
        if ev.event_type == ExecutionEventType.CAPABILITY_STARTED
    ]
    assert len(started_events) >= 1
    started = started_events[0]

    # Verify enriched metadata contains required operational fields
    assert "task_state_snapshot" in started.metadata
    assert "execution_controller_decision" in started.metadata
    assert started.metadata["capability_requested"] == "inspect_spreadsheet"
    assert started.metadata["capability_called"] == "inspect_spreadsheet"
    assert "allowed_next_actions" in started.metadata

    # Verify task_state_snapshot contents
    snapshot = started.metadata["task_state_snapshot"]
    assert snapshot["user_id"] == "charlie"
    assert snapshot["selected_skill"] == WorkflowName.COMPUTATION.value


def test_ui_service_grouped_audit_and_diagnostics(tmp_path: Path):
    audit_file = tmp_path / "events.jsonl"
    audit_service = PersistentAuditService(log_path=audit_file)
    backend = UIBackendService(audit_service=audit_service)

    # Login as admin
    ok, msg, user, token = backend.login("admin", "adminpass")
    assert ok and token is not None

    session_id = uuid4()
    task_id = uuid4()

    audit_service.record_event(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="alice",
            event_type=ExecutionEventType.MODEL_INVOKED,
            component="agent_runtime",
            status=ExecutionEventStatus.COMPLETED,
            summary="Model generated intent plan",
            model_id="agent_model",
            metadata={"model_raw_response": {"intent": "spreadsheet_analysis", "confidence": 0.95}},
        )
    )

    audit_service.record_event(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="alice",
            event_type=ExecutionEventType.CAPABILITY_STARTED,
            component="execution_controller",
            status=ExecutionEventStatus.STARTED,
            summary="Calling inspect_spreadsheet",
            capability_id="inspect_spreadsheet",
            metadata={
                "task_state_snapshot": {"status": "in_progress"},
                "execution_controller_decision": {"action": "inspect_spreadsheet"},
                "capability_requested": "inspect_spreadsheet",
                "capability_called": "inspect_spreadsheet",
            },
        )
    )

    # Check grouped logs
    grouped = backend.get_admin_audit_grouped(token)
    assert "alice" in grouped
    assert str(session_id) in grouped["alice"]
    assert str(task_id) in grouped["alice"][str(session_id)]

    # Check diagnostic summary
    diag = backend.get_admin_audit_diagnostic_summary(
        token, "alice", str(session_id), str(task_id)
    )
    assert diag["total_events"] == 2
    assert len(diag["model_responses"]) == 1
    assert diag["model_responses"][0]["model_raw_response"]["intent"] == "spreadsheet_analysis"
    assert len(diag["task_state_snapshots"]) == 1
    assert diag["capabilities_requested"] == ["inspect_spreadsheet"]
    assert diag["capabilities_called"] == ["inspect_spreadsheet"]

    # Check live feed
    feed = backend.get_admin_audit_live_feed(token)
    assert "inspect_spreadsheet" in feed


def test_sessions_sorted_by_timestamp_descending(tmp_path: Path):
    from datetime import datetime, timezone, timedelta
    log_file = tmp_path / "events.jsonl"
    service = PersistentAuditService(log_path=log_file)

    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    s_old = uuid4()
    s_mid = uuid4()
    s_new = uuid4()

    # Record in mixed order
    service.record_event(
        ExecutionEvent(
            session_id=s_old,
            task_id=uuid4(),
            user_id="alice",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Old session",
            timestamp=t0,
        )
    )
    service.record_event(
        ExecutionEvent(
            session_id=s_new,
            task_id=uuid4(),
            user_id="alice",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="New session",
            timestamp=t2,
        )
    )
    service.record_event(
        ExecutionEvent(
            session_id=s_mid,
            task_id=uuid4(),
            user_id="alice",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Mid session",
            timestamp=t1,
        )
    )

    reader = AuditLogReader(log_path=log_file)
    sessions = reader.get_sessions("alice")
    # Latest session must appear first
    assert sessions == [str(s_new), str(s_mid), str(s_old)]


def test_ollama_model_tag_resolution_with_model_configs():
    from aegis.config import ModelConfig
    from aegis.router import OllamaModelProvider

    configs = [
        ModelConfig(
            id="agent_model",
            name="Qwen3 8B Agent",
            provider="local_ollama",
            provider_model_id="qwen3:8b",
            roles=["agent"],
            capabilities=["reasoning"],
            modalities=["text"],
            task_types=["general_reasoning"],
            context_window=8192,
        ),
        ModelConfig(
            id="coding_model",
            name="Qwen2.5 Coder",
            provider="local_ollama",
            provider_model_id="qwen2.5-coder:7b",
            roles=["coding"],
            capabilities=["code_generation"],
            modalities=["text"],
            task_types=["code_generation"],
            context_window=8192,
        ),
    ]

    # Without provider_config, should still map agent_model -> qwen3:8b
    provider = OllamaModelProvider(
        base_url="http://example.invalid:11434",
        model_configs=configs,
    )
    assert provider.resolve_model_tag("agent_model") == "qwen3:8b"
    assert provider.resolve_model_tag("coding_model") == "qwen2.5-coder:7b"


def test_diagnostic_summary_captures_all_models_and_prompts(tmp_path: Path):
    log_file = tmp_path / "events.jsonl"
    audit_service = PersistentAuditService(log_path=log_file)
    backend = UIBackendService(audit_service=audit_service)
    ok, _, _, token = backend.login("admin", "adminpass")
    assert ok and token is not None
    session_id = uuid4()
    task_id = uuid4()

    # Agent model event
    audit_service.record_event(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="alice",
            event_type=ExecutionEventType.MODEL_INVOKED,
            component="agent_runtime",
            status=ExecutionEventStatus.COMPLETED,
            summary="Agent model invoked",
            model_id="agent_model",
            model_provider_id="local_ollama",
            metadata={
                "prompt": "Classify this intent",
                "system_prompt": "Output JSON only",
                "model_raw_response": {"intent": "computation"},
                "role": "agent",
                "task_type": "intent_analysis",
            },
        )
    )

    # Coding model event
    audit_service.record_event(
        ExecutionEvent(
            session_id=session_id,
            task_id=task_id,
            user_id="alice",
            event_type=ExecutionEventType.MODEL_INVOKED,
            component="generate_code",
            status=ExecutionEventStatus.COMPLETED,
            summary="Coding model invoked",
            model_id="coding_model",
            model_provider_id="local_ollama",
            metadata={
                "prompt": "Generate Python calculation",
                "system_prompt": "Executable Python only",
                "model_raw_response": "import openpyxl\nprint(1)",
                "role": "coding",
                "task_type": "code_generation",
            },
        )
    )

    diag = backend.get_admin_audit_diagnostic_summary(
        token, "alice", str(session_id), str(task_id)
    )
    assert len(diag["model_responses"]) == 2
    # Verify agent model
    agent_resp = diag["model_responses"][0]
    assert agent_resp["model_id"] == "agent_model"
    assert agent_resp["role"] == "agent"
    assert agent_resp["prompt"] == "Classify this intent"
    assert agent_resp["system_prompt"] == "Output JSON only"
    assert agent_resp["model_raw_response"]["intent"] == "computation"

    # Verify coding model
    coding_resp = diag["model_responses"][1]
    assert coding_resp["model_id"] == "coding_model"
    assert coding_resp["role"] == "coding"
    assert coding_resp["prompt"] == "Generate Python calculation"
    assert coding_resp["system_prompt"] == "Executable Python only"
    assert "import openpyxl" in coding_resp["model_raw_response"]

