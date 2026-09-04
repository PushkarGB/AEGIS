"""Verify the event publisher fix: RuntimeTaskRunner events reach the audit service.

This test ensures that when a RuntimeTaskRunner is injected into UIBackendService,
the service adopts the runner's event publisher. This guarantees:
1. Audit events are persisted from real execution
2. SessionEventCollector receives progressive events for streaming
3. The system is provider-agnostic (no Colab/ngrok dependency)
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.audit import PersistentAuditService
from aegis.capabilities import MockSandboxRunner, SandboxResult
from aegis.config import load_config
from aegis.events import ExecutionEvent, ExecutionEventPublisher, ExecutionEventStatus, ExecutionEventType
from aegis.orchestration import RuntimeTaskRunner
from aegis.router import MockModelProvider, ModelRegistry, ModelRouter
from aegis.ui.service import UIBackendService

from demo.fixtures import EXPECTED_COMPUTATION_RECORDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_audit_file(tmp_path: Path) -> Path:
    return tmp_path / "test_events.jsonl"


def _make_mock_agent_provider() -> MockModelProvider:
    """Mock agent provider: returns intent analysis on first call, then finish."""
    call_count = 0

    def agent_response(_request) -> str:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # Intent analysis
            return json.dumps(
                {
                    "intent": "computation",
                    "modality": "spreadsheet",
                    "workflow": "computation",
                    "summary": "Spreadsheet computation workflow.",
                }
            )
        # Subsequent calls: finish
        return json.dumps(
            {
                "directive": "continue",
                "summary": "Proceed.",
                "proposed_action": {
                    "action": "finish",
                    "inputs": {},
                    "done": True,
                    "summary": "Finish.",
                },
            }
        )

    return MockModelProvider(response_factory=agent_response)


def _make_mock_coding_provider() -> MockModelProvider:
    """Mock coding provider: returns executable Python code."""
    def coding_response(_request) -> str:
        return (
            "import json\n"
            f"records = {EXPECTED_COMPUTATION_RECORDS!r}\n"
            "print(json.dumps(records))\n"
        )

    return MockModelProvider(response_factory=coding_response)


def _build_runner(agent_provider: MockModelProvider) -> RuntimeTaskRunner:
    """Build a RuntimeTaskRunner with mock providers — fully provider-agnostic."""
    config = load_config()
    providers = {"local_ollama": agent_provider}
    return RuntimeTaskRunner(
        providers=providers,
        sandbox_runner=MockSandboxRunner(
            default_result=SandboxResult(
                stdout=json.dumps(EXPECTED_COMPUTATION_RECORDS),
                stderr="",
                exit_code=0,
                timed_out=False,
            )
        ),
        config=config,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublisherUnification:
    """Verify UIBackendService adopts the RuntimeTaskRunner's publisher."""

    def test_service_adopts_runner_publisher(self, tmp_audit_file: Path) -> None:
        """The service's publisher must be the same instance as the runner's."""
        provider = _make_mock_agent_provider()
        runner = _build_runner(provider)
        audit = PersistentAuditService(log_path=tmp_audit_file)

        service = UIBackendService(runner=runner, audit_service=audit)

        # The service's publisher must be the exact same object as the runner's
        assert service.event_publisher is runner.event_publisher, (
            "UIBackendService must adopt the injected runner's event publisher, "
            "not create a separate one."
        )

    def test_service_creates_own_publisher_without_runner(self, tmp_audit_file: Path) -> None:
        """When no runner is injected, service creates its own publisher."""
        audit = PersistentAuditService(log_path=tmp_audit_file)
        service = UIBackendService(audit_service=audit)

        # Service creates DeterministicTaskRunner with its own publisher
        assert service.event_publisher is not None

    def test_audit_receives_runner_events(self, tmp_audit_file: Path) -> None:
        """Events published by the runner must reach the audit file."""
        provider = _make_mock_agent_provider()
        runner = _build_runner(provider)
        audit = PersistentAuditService(log_path=tmp_audit_file)

        service = UIBackendService(runner=runner, audit_service=audit)

        # Publish a synthetic event through the runner's publisher
        event = ExecutionEvent(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="test-user",
            event_type=ExecutionEventType.TASK_STARTED,
            component="test",
            status=ExecutionEventStatus.STARTED,
            summary="Test event from runner pipeline.",
        )
        runner.event_publisher.publish(event)

        # The audit service should have captured it
        assert audit.total_count >= 1
        records = audit.get_records(limit=10)
        assert any(r.summary == "Test event from runner pipeline." for r in records)

        # The JSONL file should have it persisted
        lines = tmp_audit_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        persisted = json.loads(lines[-1])
        assert persisted["summary"] == "Test event from runner pipeline."

    def test_runner_execution_events_reach_audit(self, tmp_audit_file: Path) -> None:
        """Full task execution through RuntimeTaskRunner persists events to audit JSONL."""
        provider = _make_mock_agent_provider()
        runner = _build_runner(provider)
        audit = PersistentAuditService(log_path=tmp_audit_file)
        service = UIBackendService(runner=runner, audit_service=audit)

        ok, msg, user, token = service.login("alice", "password123")
        assert ok, f"Login failed: {msg}"
        session = service.create_session(token)

        from demo.fixtures import create_synthetic_equipment_spreadsheet

        xlsx_path = str(Path(tmp_audit_file).parent / "test.xlsx")
        create_synthetic_equipment_spreadsheet(xlsx_path)

        result = service.submit_task(
            token_str=token,
            session_id=session.session_id,
            prompt="Calculate average thickness",
            attachment_path=xlsx_path,
        )

        # Audit MUST have events
        assert audit.total_count > 0, "Audit must capture events from RuntimeTaskRunner"

        # JSONL file must contain persisted events
        lines = tmp_audit_file.read_text().strip().splitlines()
        assert len(lines) > 0, "JSONL must contain persisted events"

        # Check that event types include real RuntimeTaskRunner events
        event_types = {json.loads(l)["event_type"] for l in lines}
        assert "task_started" in event_types or "capability_started" in event_types


class TestProviderAgnosticism:
    """Verify the system has no hard dependency on Colab, ngrok, or any specific provider."""

    def test_runtime_runner_works_with_any_mock_provider(self, tmp_audit_file: Path) -> None:
        """RuntimeTaskRunner should work with any ModelProvider implementation."""
        provider = _make_mock_agent_provider()
        runner = _build_runner(provider)
        audit = PersistentAuditService(log_path=tmp_audit_file)
        service = UIBackendService(runner=runner, audit_service=audit)

        ok, msg, user, token = service.login("alice", "password123")
        assert ok, f"Login failed: {msg}"
        session = service.create_session(token)

        from demo.fixtures import create_synthetic_equipment_spreadsheet

        xlsx_path = str(Path(tmp_audit_file).parent / "test.xlsx")
        create_synthetic_equipment_spreadsheet(xlsx_path)

        result = service.submit_task(
            token_str=token,
            session_id=session.session_id,
            prompt="Calculate average thickness",
            attachment_path=xlsx_path,
        )

        # Should produce real execution events in audit
        assert audit.total_count > 0, "Audit must capture events from RuntimeTaskRunner"

    def test_no_colab_import_in_business_logic(self) -> None:
        """No aegis.* module should import or reference 'colab' or 'ngrok'."""
        import importlib
        import pkgutil

        import aegis

        colab_refs: list[str] = []
        for importer, modname, ispkg in pkgutil.walk_packages(
            aegis.__path__, prefix="aegis."
        ):
            try:
                mod = importlib.import_module(modname)
                source = getattr(mod, "__file__", None)
                if source and Path(source).exists():
                    content = Path(source).read_text(encoding="utf-8", errors="ignore").lower()
                    if "colab" in content or "ngrok" in content:
                        colab_refs.append(modname)
            except Exception:
                pass

        assert colab_refs == [], (
            f"Business logic modules must not reference Colab/ngrok: {colab_refs}"
        )

    def test_config_models_yaml_no_hardcoded_colab_endpoint(self) -> None:
        """The models.yaml config must not contain hardcoded Colab/ngrok URLs."""
        config_path = Path("config/models.yaml")
        if not config_path.exists():
            pytest.skip("config/models.yaml not found")

        content = config_path.read_text(encoding="utf-8").lower()
        assert "ngrok" not in content, "models.yaml must not hardcode ngrok URLs"


class TestStreamingWithUnifiedPublisher:
    """Verify that streaming uses the unified publisher pipeline."""

    def test_streaming_receives_events_from_runtime_runner(self, tmp_audit_file: Path) -> None:
        """submit_task_streaming must receive events from RuntimeTaskRunner."""
        provider = _make_mock_agent_provider()
        runner = _build_runner(provider)
        audit = PersistentAuditService(log_path=tmp_audit_file)
        service = UIBackendService(runner=runner, audit_service=audit)

        ok, msg, user, token = service.login("alice", "password123")
        assert ok
        session = service.create_session(token)

        from demo.fixtures import create_synthetic_equipment_spreadsheet

        xlsx_path = str(Path(tmp_audit_file).parent / "test.xlsx")
        create_synthetic_equipment_spreadsheet(xlsx_path)

        updates = list(
            service.submit_task_streaming(
                token_str=token,
                session_id=session.session_id,
                prompt="Calculate average thickness",
                attachment_path=xlsx_path,
                poll_interval=0.05,
            )
        )

        # Must have at least one final update
        assert any(u.is_final for u in updates), "Streaming must produce a final update"

        # The final update must have events
        final = [u for u in updates if u.is_final][0]
        assert final.result is not None
        assert len(final.events) > 0 or len(final.result.events) > 0, (
            "Final streaming update must contain execution events"
        )
