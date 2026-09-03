"""Deterministic Controller that governs workflow state and capability calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from aegis.broker import CapabilityBroker
from aegis.schemas import (
    AgentDecision,
    ApprovalStatus,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    Observation,
    TaskState,
    VerificationResult,
    VerificationStatus,
)

from .workflows import WorkflowDefinition, WorkflowName, get_workflow


class ExecutionEventKind(StrEnum):
    """High-level events safe to expose in the execution UI."""

    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_REJECTED = "action_rejected"
    APPROVAL_RECORDED = "approval_recorded"
    LIMIT_EXCEEDED = "limit_exceeded"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """A concise Controller event without model reasoning or chain-of-thought."""

    sequence: int
    kind: ExecutionEventKind
    summary: str
    action: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionController:
    """Own and govern one TaskState through a bounded declarative workflow."""

    def __init__(
        self,
        state: TaskState,
        workflow: WorkflowName | str | WorkflowDefinition,
        broker: CapabilityBroker,
    ) -> None:
        self.state = state
        self.workflow = (
            workflow if isinstance(workflow, WorkflowDefinition) else get_workflow(workflow)
        )
        self._broker = broker
        self._events: list[ExecutionEvent] = []
        self.last_action: str | None = None
        self.last_capability_result: CapabilityResult | None = None

        if self.state.final_status != FinalStatus.NOT_FINAL:
            raise ValueError("ExecutionController requires a non-terminal TaskState")
        if self.state.selected_skill not in {None, self.workflow.name.value}:
            raise ValueError("TaskState selected_skill must match the selected workflow")

        self.state.selected_skill = self.workflow.name.value
        if self.state.current_step is None:
            self.state.current_step = self.workflow.start_state
        if self.workflow.requires_approval and self.state.approval_status == ApprovalStatus.NOT_REQUIRED:
            self.state.approval_status = ApprovalStatus.PENDING

    @property
    def execution_events(self) -> tuple[ExecutionEvent, ...]:
        """Expose the ordered, high-level event stream for this task."""

        return tuple(self._events)

    def observation_for_agent(self) -> Observation:
        """Return the latest capability observation for Agent reasoning.

        Exposes the domain/capability observation rather than internal
        Controller governance wrappers so the Agent reasons on real outputs.
        """
        if self.last_capability_result is not None and self.last_capability_result.observations:
            return self.last_capability_result.observations[-1]

        for observation in reversed(self.state.observations):
            if observation.source != "execution_controller":
                return observation

        if self.state.observations:
            return self.state.observations[-1]

        return Observation(
            source="execution_controller",
            kind="task_initialized",
            summary=self.state.user_goal,
            data={"current_step": self.state.current_step},
        )

    def allowed_next_actions(self) -> tuple[str, ...]:
        """Return legal capability actions from the current workflow state."""
        if self.state.final_status != FinalStatus.NOT_FINAL:
            return ()
        return tuple(
            sorted(
                self.workflow.allowed_actions(
                    self.state.current_step, self.state.approval_status
                )
            )
        )

    def execute(self, decision: AgentDecision) -> ExecutionEvent:
        """Validate and execute one Agent proposal through the Broker boundary."""

        rejection_reason = self._rejection_reason(decision)
        if rejection_reason:
            return self._emit(
                ExecutionEventKind.ACTION_REJECTED,
                rejection_reason,
                decision.action,
            )

        if self.state.iteration_count >= self.state.max_iterations:
            self.state.final_status = FinalStatus.FAILED
            self._record_controller_observation(
                "iteration_limit", "Iteration limit exhausted before capability invocation.", decision.action
            )
            return self._emit(
                ExecutionEventKind.LIMIT_EXCEEDED,
                "Iteration limit exhausted; task failed.",
                decision.action,
            )

        request = CapabilityRequest(
            capability_name=decision.action,
            inputs=decision.inputs,
            task_id=self.state.session_id,
        )
        self.state.iteration_count += 1
        self.last_action = decision.action
        self._emit(
            ExecutionEventKind.ACTION_STARTED,
            f"Invoking {decision.action} through the Capability Broker.",
            decision.action,
        )

        try:
            result = self._broker.invoke(request)
        except Exception as error:  # Broker adapters must not escape Controller governance.
            result = CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Capability Broker raised {type(error).__name__}: {error}",
            )

        if result.request_id != request.request_id:
            result = CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Capability Broker returned a result for a different request.",
            )

        self.last_capability_result = result
        self.state.observations.extend(result.observations)
        self.state.generated_artifacts.extend(result.artifacts)

        if result.status == CapabilityResultStatus.SUCCEEDED:
            return self._handle_success(decision.action, result)
        return self._handle_failure(decision.action, result)

    def record_approval(self, approved: bool) -> ExecutionEvent:
        """Record an external human approval decision after successful verification."""

        if not self.workflow.requires_approval:
            return self._emit(
                ExecutionEventKind.ACTION_REJECTED,
                "This workflow does not require human approval.",
            )
        if self.state.final_status != FinalStatus.NOT_FINAL:
            return self._emit(
                ExecutionEventKind.ACTION_REJECTED,
                "Terminal tasks cannot receive approval decisions.",
            )
        if self.state.current_step != "finish" or self.state.verification_status != VerificationStatus.PASSED:
            return self._emit(
                ExecutionEventKind.ACTION_REJECTED,
                "Approval is allowed only after verification passes.",
            )

        if approved:
            self.state.approval_status = ApprovalStatus.APPROVED
            return self._emit(
                ExecutionEventKind.APPROVAL_RECORDED,
                "Human approval recorded; workflow may finish.",
            )

        self.state.approval_status = ApprovalStatus.REJECTED
        self.state.final_status = FinalStatus.CANCELLED
        self._record_controller_observation(
            "approval_rejected", "Human approval was rejected; task cancelled.", None
        )
        return self._emit(
            ExecutionEventKind.APPROVAL_RECORDED,
            "Human rejection recorded; task cancelled.",
        )

    def _rejection_reason(self, decision: AgentDecision) -> str | None:
        if self.state.final_status != FinalStatus.NOT_FINAL:
            return "Task is terminal and cannot accept further actions."
        if decision.done != (decision.action == "finish"):
            return "Only the finish action may set done=true."
        if not self.workflow.allows(
            self.state.current_step, decision.action, self.state.approval_status
        ):
            return (
                f"Action '{decision.action}' is not allowed from workflow state "
                f"'{self.state.current_step}'."
            )
        return None

    def _handle_success(
        self, action: str, result: CapabilityResult
    ) -> ExecutionEvent:
        self._record_controller_observation(
            "capability_succeeded", f"Capability {action} completed successfully.", action, result.request_id
        )
        if action not in self.state.completed_steps:
            self.state.completed_steps.append(action)

        if action == "verify_result":
            self.state.verification_status = VerificationStatus.PASSED
            self.state.verification_results.append(
                VerificationResult(
                    status=VerificationStatus.PASSED,
                    verifier="verify_result",
                    summary="Verification capability completed successfully.",
                    details=result.output,
                    artifact_ids=[artifact.artifact_id for artifact in result.artifacts],
                )
            )

        previous_state = self.state.current_step
        self.state.current_step = self.workflow.next_state_on_success(previous_state, action)
        self._emit(
            ExecutionEventKind.ACTION_COMPLETED,
            f"Capability {action} completed.",
            action,
        )
        if action == "finish":
            self.state.final_status = FinalStatus.COMPLETED
            return self._emit(
                ExecutionEventKind.TASK_COMPLETED,
                "Workflow completed.",
                action,
            )
        return self._events[-1]

    def _handle_failure(self, action: str, result: CapabilityResult) -> ExecutionEvent:
        self._record_controller_observation(
            "capability_failed", result.error or f"Capability {action} failed.", action, result.request_id
        )
        if action == "verify_result":
            self.state.verification_status = VerificationStatus.FAILED
            self.state.verification_results.append(
                VerificationResult(
                    status=VerificationStatus.FAILED,
                    verifier="verify_result",
                    summary=result.error or "Verification capability failed.",
                    details=result.output,
                    artifact_ids=[artifact.artifact_id for artifact in result.artifacts],
                )
            )

        if self.state.retry_count >= self.state.max_retries:
            self.state.final_status = FinalStatus.FAILED
            self._emit(
                ExecutionEventKind.ACTION_FAILED,
                f"Capability {action} failed and retry limit is exhausted.",
                action,
            )
            return self._emit(
                ExecutionEventKind.TASK_FAILED,
                "Task failed after exhausting retries.",
                action,
            )

        self.state.retry_count += 1
        self.state.current_step = self.workflow.next_state_on_failure(
            self.state.current_step, action
        )
        return self._emit(
            ExecutionEventKind.ACTION_FAILED,
            f"Capability {action} failed; corrective action is required.",
            action,
        )

    def _record_controller_observation(
        self,
        kind: str,
        summary: str,
        action: str | None,
        request_id=None,
    ) -> None:
        self.state.observations.append(
            Observation(
                source="execution_controller",
                kind=kind,
                summary=summary,
                data={"action": action} if action else {},
                request_id=request_id,
            )
        )

    def _emit(
        self, kind: ExecutionEventKind, summary: str, action: str | None = None
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            sequence=len(self._events) + 1,
            kind=kind,
            summary=summary,
            action=action,
            occurred_at=datetime.now(timezone.utc),
        )
        self._events.append(event)
        return event
