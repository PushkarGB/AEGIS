"""Deterministic task execution runner for UI development.

Uses the authoritative ExecutionController, deterministic mock capabilities,
and standard ExecutionEventPublisher to simulate realistic multi-step workflow
executions and Human-In-The-Loop (HITL) approval states without cloud APIs
or heavy local model inference.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from aegis.broker import CapabilityBroker
from aegis.events import (
    ExecutionEvent,
    ExecutionEventContext,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration import ExecutionController, WorkflowName, get_workflow
from aegis.orchestration.hitl import HITLApprovalState
from aegis.schemas import (
    AgentDecision,
    ApprovalStatus,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    Observation,
    TaskState,
    VerificationStatus,
)
from aegis.ui.event_stream import MOCK_EVENT_PACE_SECONDS


@dataclass(frozen=True)
class ExecutionRunResult:
    """Outcome of running or resuming a task execution."""

    task_id: UUID
    session_id: UUID
    workflow_id: str
    final_status: FinalStatus
    hitl_state: HITLApprovalState | None
    events: list[ExecutionEvent]
    result_text: str
    artifact_paths: list[str]


class DeterministicTaskRunner:
    """Governs mock workflow runs through the authoritative ExecutionController.

    Holds in-flight controllers to allow asynchronous HITL approval or rejection.
    All execution events are streamed to the provided ExecutionEventPublisher.
    """

    def __init__(
        self,
        event_publisher: ExecutionEventPublisher,
        event_pace_seconds: float = MOCK_EVENT_PACE_SECONDS,
    ) -> None:
        self._publisher = event_publisher
        self._event_pace = event_pace_seconds
        self._active_controllers: dict[UUID, ExecutionController] = {}
        self._deliverables: dict[UUID, tuple[str, list[str]]] = {}

    def get_controller(self, task_id: UUID) -> ExecutionController | None:
        """Return the active ExecutionController for a task, if any."""
        return self._active_controllers.get(task_id)

    def start_execution(
        self,
        session_id: UUID,
        task_id: UUID,
        user_id: str,
        user_goal: str,
        attachment_path: str | None = None,
    ) -> ExecutionRunResult:
        """Start deterministic workflow execution for a task."""
        workflow_name = self._infer_workflow(user_goal, attachment_path)
        if workflow_name is None:
            started_event = ExecutionEvent(
                session_id=session_id,
                task_id=task_id,
                user_id=user_id,
                event_type=ExecutionEventType.TASK_STARTED,
                component="execution_controller",
                status=ExecutionEventStatus.STARTED,
                summary="Understanding request",
            )
            failed_event = ExecutionEvent(
                session_id=session_id,
                task_id=task_id,
                user_id=user_id,
                event_type=ExecutionEventType.TASK_FAILED,
                component="execution_controller",
                status=ExecutionEventStatus.FAILED,
                summary="Unsupported or ambiguous request. No supported workflow determined.",
                metadata={"reason": "unsupported_or_ambiguous_request"},
            )
            self._publisher.publish(started_event)
            self._publisher.publish(failed_event)

            result_text = (
                "### Unsupported or Ambiguous Request\n\n"
                "The requested task could not be deterministically routed to a supported prototype workflow. "
                "In mock/demo mode, arbitrary natural language is not processed by a real model.\n\n"
                "**Supported Tasks:**\n"
                "1. **Engineering Computation**: Calculate average thickness and threshold checks from equipment readings (attach `.xlsx`, `.csv` or request calculation/thickness readings).\n"
                "2. **Scanned Document Approval**: Extract and review inspection reports for conditional approval notes (attach `.pdf` or request scanned report/approval note review).\n"
                "3. **Multimodal Analysis**: Inspect equipment photographs for defects or corrosion (attach `.png`, `.jpg` or request photograph/image analysis).\n\n"
                "Please provide a supported task request or attach a relevant file."
            )

            return ExecutionRunResult(
                task_id=task_id,
                session_id=session_id,
                workflow_id="none",
                final_status=FinalStatus.FAILED,
                hitl_state=None,
                events=[started_event, failed_event],
                result_text=result_text,
                artifact_paths=[],
            )

        attachments = [attachment_path] if attachment_path else []

        state = TaskState(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            user_goal=user_goal,
            attachments=attachments,
        )

        broker = self._create_mock_broker(workflow_name)
        controller = ExecutionController(
            state=state,
            workflow=workflow_name,
            broker=broker,
            event_publisher=self._publisher,
        )
        self._active_controllers[task_id] = controller

        return self._run_steps(controller, workflow_name)

    def record_approval(
        self,
        task_id: UUID,
        user_id: str,
        approved: bool,
    ) -> ExecutionRunResult:
        """Record human approval/rejection for an awaiting task and continue."""
        controller = self._active_controllers.get(task_id)
        if controller is None:
            raise ValueError(f"No active controller found for task {task_id}")

        event = controller.record_approval(approved=approved, user_id=user_id)
        if event.status == ExecutionEventStatus.REJECTED:
            raise ValueError(f"Approval transition rejected by Controller: {event.summary}")

        if not approved:
            # Rejection was recorded
            result_text = "Task was rejected by operator.\n\n[Status: REJECTED]"
            self._deliverables[task_id] = (result_text, [])
            return ExecutionRunResult(
                task_id=task_id,
                session_id=controller.state.session_id,
                workflow_id=controller.workflow.name.value,
                final_status=controller.state.final_status,
                hitl_state=controller.hitl_state,
                events=list(controller.execution_events),
                result_text=result_text,
                artifact_paths=[],
            )

        # Approved: proceed to finish
        controller.execute(AgentDecision(action="finish", done=True))
        result_text, artifacts = self._deliverables.get(
            task_id,
            ("Approval workflow completed successfully.", ["deliverables/approval_note.docx"]),
        )
        result_text += "\n\n[Status: APPROVED and FINALIZED]"

        return ExecutionRunResult(
            task_id=task_id,
            session_id=controller.state.session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifacts,
        )

    def _infer_workflow(
        self, user_goal: str, attachment_path: str | None
    ) -> WorkflowName | None:
        """Deterministically infer the workflow from goal and attachment.

        Returns None if the request is unknown, unsupported, or ambiguous.
        """
        lower_goal = user_goal.lower().strip()
        ext = Path(attachment_path).suffix.lower() if attachment_path else ""

        # Multimodal indicators
        has_image_ext = ext in {".png", ".jpg", ".jpeg", ".webp"}
        image_keywords = {"image", "photo", "photograph", "visual", "corrosion", "visible condition"}
        has_image_intent = any(kw in lower_goal for kw in image_keywords)
        is_multimodal = has_image_ext or has_image_intent

        # Document approval indicators
        has_doc_ext = ext in {".pdf"}
        doc_keywords = {
            "approval note",
            "approval clearance",
            "scanned",
            "ocr",
            "inspection report",
            "draft approval",
            "extract document",
            "approval",
            "report",
        }
        has_doc_intent = any(kw in lower_goal for kw in doc_keywords)
        is_doc = has_doc_ext or has_doc_intent

        # Computation indicators
        has_sheet_ext = ext in {".xlsx", ".xls", ".csv"}
        comp_keywords = {
            "calculate",
            "calculation",
            "comput",
            "thickness",
            "reading",
            "readings",
            "spreadsheet",
            "workbook",
            "average measured",
            "threshold",
        }
        has_comp_intent = any(kw in lower_goal for kw in comp_keywords)
        is_comp = has_sheet_ext or has_comp_intent

        # Check for cross-intent / conflicting indicators
        intents_count = sum([has_image_intent, has_doc_intent, has_comp_intent])
        if intents_count > 1:
            return None

        # Build candidate matches matching file type and/or intent
        matches: list[WorkflowName] = []
        if is_multimodal and not (has_sheet_ext or has_doc_ext):
            matches.append(WorkflowName.MULTIMODAL_ANALYSIS)
        if is_doc and not (has_sheet_ext or has_image_ext):
            matches.append(WorkflowName.SCANNED_DOCUMENT_APPROVAL)
        if is_comp and not (has_doc_ext or has_image_ext):
            matches.append(WorkflowName.COMPUTATION)

        # Ambiguous if multiple matched, unsupported if none matched
        if len(matches) == 1:
            return matches[0]
        return None

    def _run_steps(
        self, controller: ExecutionController, workflow_name: WorkflowName
    ) -> ExecutionRunResult:
        """Execute steps until terminal or HITL pause."""
        task_id = controller.state.task_id

        if workflow_name == WorkflowName.COMPUTATION:
            steps = [
                ("inspect_spreadsheet", {}),
                ("generate_code", {"language": "python"}),
                ("run_code", {"timeout": 30}),
                ("verify_result", {}),
                ("generate_excel", {"template": "industrial_readings"}),
                ("finish", {}),
            ]
            for action, inputs in steps:
                if self._event_pace > 0:
                    time.sleep(self._event_pace)
                done = (action == "finish")
                controller.execute(AgentDecision(action=action, inputs=inputs, done=done))

            result_text = (
                "### Computation Result\n\n"
                "- Average measured thickness across equipment: **7.42 mm**\n"
                "- Equipment below minimum acceptable threshold: **EQ-104 (4.10 mm < 5.00 mm)**, "
                "**EQ-208 (3.85 mm < 4.50 mm)**\n"
                "- Verification: **PASSED** (150 readings analyzed, sandbox network disabled)\n"
                "- Generated deliverable: `deliverables/inspection_summary.xlsx`"
            )
            artifacts = ["deliverables/inspection_summary.xlsx"]
            self._deliverables[task_id] = (result_text, artifacts)

        elif workflow_name == WorkflowName.SCANNED_DOCUMENT_APPROVAL:
            # Run steps up to verify_result; verify_result triggers WAITING_FOR_APPROVAL
            pre_hitl_steps = [
                ("extract_document", {}),
                ("ocr_document", {}),
                ("draft_approval_note", {}),
                ("generate_word", {}),
                ("verify_result", {}),
            ]
            for action, inputs in pre_hitl_steps:
                if self._event_pace > 0:
                    time.sleep(self._event_pace)
                controller.execute(AgentDecision(action=action, inputs=inputs, done=False))

            result_text = (
                "### Draft Approval Note Generated\n\n"
                "- Document: **Pressure Vessel Inspection Report (PV-2026-09)**\n"
                "- Key Findings: Shell thickness within tolerance; minor flange pitting detected.\n"
                "- Recommendation: Approve conditional operating clearance for 180 days.\n"
                "- Verification: **PASSED** (grounded in extracted report findings)\n\n"
                "**Awaiting Human Approval**: Review findings above and click Approve or Reject."
            )
            artifacts = ["deliverables/approval_note_draft.docx"]
            self._deliverables[task_id] = (result_text, artifacts)

        elif workflow_name == WorkflowName.MULTIMODAL_ANALYSIS:
            steps = [
                ("analyze_image", {}),
                ("verify_result", {}),
                ("finish", {}),
            ]
            for action, inputs in steps:
                if self._event_pace > 0:
                    time.sleep(self._event_pace)
                done = (action == "finish")
                controller.execute(AgentDecision(action=action, inputs=inputs, done=done))

            result_text = (
                "### Multimodal Inspection Analysis\n\n"
                "- Modality: Industrial equipment photograph\n"
                "- Observation: Surface corrosion on pipe weld joint (Zone 3B)\n"
                "- Severity: Moderate — ultrasonic test recommended\n"
                "- Verification: **PASSED**"
            )
            artifacts = []
            self._deliverables[task_id] = (result_text, artifacts)

        return ExecutionRunResult(
            task_id=task_id,
            session_id=controller.state.session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifacts,
        )

    def _create_mock_broker(self, workflow_name: WorkflowName) -> CapabilityBroker:
        """Create a deterministic MockCapabilityBroker returning successful results."""

        class _MockBroker(CapabilityBroker):
            def invoke(self, request: CapabilityRequest) -> CapabilityResult:
                obs = Observation(
                    source=request.capability_name,
                    kind="step_completed",
                    summary=f"Mock capability '{request.capability_name}' completed successfully.",
                    data={"inputs": request.inputs},
                )
                return CapabilityResult(
                    request_id=request.request_id,
                    status=CapabilityResultStatus.SUCCEEDED,
                    output={"status": "ok", "action": request.capability_name},
                    observations=[obs],
                )

        return _MockBroker()
