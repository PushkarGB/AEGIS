"""Tests proving deterministic mock workflow routing behavior.

Verifies:
1. Known supported computation request -> selects computation workflow.
2. Known supported document request -> selects document workflow.
3. Known supported multimodal request -> selects multimodal workflow.
4. Unknown, unsupported, or ambiguous request:
   - Does NOT select a random workflow.
   - Returns a clear "unsupported/ambiguous request" result.
   - Prompts the user to provide a supported task.
5. Routing is strictly deterministic (repeated identical requests produce the same result).
6. Does not pretend mock understands arbitrary natural language.
"""

from __future__ import annotations

from uuid import uuid4
import pytest

from aegis.events import ExecutionEventPublisher, ExecutionEventType, ExecutionEventStatus
from aegis.orchestration import WorkflowName
from aegis.schemas import FinalStatus
from aegis.ui.runner import DeterministicTaskRunner, ExecutionRunResult


@pytest.fixture
def task_runner() -> DeterministicTaskRunner:
    publisher = ExecutionEventPublisher()
    return DeterministicTaskRunner(event_publisher=publisher, event_pace_seconds=0.0)


class TestKnownWorkflowRouting:
    """Proves known supported requests route correctly and deterministically."""

    def test_known_computation_request_routes_to_computation(
        self, task_runner: DeterministicTaskRunner
    ):
        result: ExecutionRunResult = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Calculate the average measured thickness from equipment readings.",
            attachment_path="readings.xlsx",
        )
        assert result.workflow_id == WorkflowName.COMPUTATION.value
        assert result.final_status == FinalStatus.COMPLETED
        assert "average measured thickness" in result.result_text.lower()
        assert len(result.artifact_paths) > 0

    def test_known_computation_without_attachment_routes_to_computation(
        self, task_runner: DeterministicTaskRunner
    ):
        result: ExecutionRunResult = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Run thickness computation on equipment readings.",
            attachment_path=None,
        )
        assert result.workflow_id == WorkflowName.COMPUTATION.value
        assert result.final_status == FinalStatus.COMPLETED

    def test_known_document_request_routes_to_document_approval(
        self, task_runner: DeterministicTaskRunner
    ):
        result: ExecutionRunResult = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Review scanned inspection report and prepare approval note.",
            attachment_path="report.pdf",
        )
        assert result.workflow_id == WorkflowName.SCANNED_DOCUMENT_APPROVAL.value
        assert result.final_status == FinalStatus.NOT_FINAL
        assert "awaiting human approval" in result.result_text.lower()

    def test_known_multimodal_request_routes_to_multimodal(
        self, task_runner: DeterministicTaskRunner
    ):
        result: ExecutionRunResult = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Inspect equipment photograph for visible surface corrosion.",
            attachment_path="pipe.png",
        )
        assert result.workflow_id == WorkflowName.MULTIMODAL_ANALYSIS.value
        assert result.final_status == FinalStatus.COMPLETED
        assert "corrosion" in result.result_text.lower()


class TestUnknownAndAmbiguousRouting:
    """Proves unknown or ambiguous requests never randomly select a workflow."""

    def test_unknown_hello_request_rejected(self, task_runner: DeterministicTaskRunner):
        result: ExecutionRunResult = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Hello",
            attachment_path=None,
        )
        assert result.workflow_id == "none"
        assert result.final_status == FinalStatus.FAILED
        assert result.hitl_state is None
        assert "unsupported or ambiguous" in result.result_text.lower()
        assert "supported tasks" in result.result_text.lower()

        # Check emitted events
        event_types = [e.event_type for e in result.events]
        assert ExecutionEventType.TASK_STARTED in event_types
        assert ExecutionEventType.TASK_FAILED in event_types
        # Ensure no capability or workflow was run
        assert ExecutionEventType.CAPABILITY_STARTED not in event_types

    def test_unknown_arbitrary_requests_do_not_route_randomly(
        self, task_runner: DeterministicTaskRunner
    ):
        arbitrary_prompts = [
            "Tell me a joke about engineers",
            "What is the capital of France?",
            "Can you write a poem about rust?",
            "Help me write a Python web scraper",
            "ping 127.0.0.1",
        ]
        for prompt in arbitrary_prompts:
            res = task_runner.start_execution(
                session_id=uuid4(),
                task_id=uuid4(),
                user_id="alice",
                user_goal=prompt,
                attachment_path=None,
            )
            assert res.workflow_id == "none", f"Prompt '{prompt}' unexpectedly routed to {res.workflow_id}"
            assert res.final_status == FinalStatus.FAILED
            assert "unsupported or ambiguous" in res.result_text.lower()

    def test_repeated_identical_unknown_requests_produce_identical_results(
        self, task_runner: DeterministicTaskRunner
    ):
        sess_id = uuid4()
        task1 = uuid4()
        task2 = uuid4()

        res1 = task_runner.start_execution(
            session_id=sess_id,
            task_id=task1,
            user_id="alice",
            user_goal="Hello",
            attachment_path=None,
        )
        res2 = task_runner.start_execution(
            session_id=sess_id,
            task_id=task2,
            user_id="alice",
            user_goal="Hello",
            attachment_path=None,
        )

        assert res1.workflow_id == res2.workflow_id == "none"
        assert res1.final_status == res2.final_status == FinalStatus.FAILED
        assert res1.result_text == res2.result_text
        assert [e.event_type for e in res1.events] == [e.event_type for e in res2.events]

    def test_ambiguous_conflicting_intents_rejected(
        self, task_runner: DeterministicTaskRunner
    ):
        # Goal mentions both document approval note and spreadsheet calculation
        res = task_runner.start_execution(
            session_id=uuid4(),
            task_id=uuid4(),
            user_id="alice",
            user_goal="Review scanned inspection report and calculate average measured thickness readings.",
            attachment_path=None,
        )
        assert res.workflow_id == "none"
        assert res.final_status == FinalStatus.FAILED
        assert "unsupported or ambiguous" in res.result_text.lower()
