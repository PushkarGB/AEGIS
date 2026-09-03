"""Deterministic Controller that governs workflow state and capability calls."""

from __future__ import annotations

from aegis.broker import CapabilityBroker
from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
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


class ExecutionEventKind:
    """Compatibility names; prefer :class:`ExecutionEventType` for new code."""

    TASK_STARTED = ExecutionEventType.TASK_STARTED
    INTENT_IDENTIFIED = ExecutionEventType.INTENT_IDENTIFIED
    WORKFLOW_SELECTED = ExecutionEventType.WORKFLOW_SELECTED
    CAPABILITY_STARTED = ExecutionEventType.CAPABILITY_STARTED
    CAPABILITY_COMPLETED = ExecutionEventType.CAPABILITY_COMPLETED
    MODEL_SELECTED = ExecutionEventType.MODEL_SELECTED
    MODEL_INVOKED = ExecutionEventType.MODEL_INVOKED
    SANDBOX_STARTED = ExecutionEventType.SANDBOX_STARTED
    SANDBOX_COMPLETED = ExecutionEventType.SANDBOX_COMPLETED
    VERIFICATION_STARTED = ExecutionEventType.VERIFICATION_STARTED
    VERIFICATION_COMPLETED = ExecutionEventType.VERIFICATION_COMPLETED
    HITL_REQUIRED = ExecutionEventType.HITL_REQUIRED
    ACTION_STARTED = ExecutionEventType.CAPABILITY_STARTED
    ACTION_COMPLETED = ExecutionEventType.CAPABILITY_COMPLETED
    ACTION_FAILED = ExecutionEventType.CAPABILITY_COMPLETED
    ACTION_REJECTED = ExecutionEventType.CAPABILITY_REJECTED
    APPROVAL_RECORDED = ExecutionEventType.APPROVAL_RECORDED
    LIMIT_EXCEEDED = ExecutionEventType.LIMIT_EXCEEDED
    TASK_COMPLETED = ExecutionEventType.TASK_COMPLETED
    TASK_FAILED = ExecutionEventType.TASK_FAILED


class ExecutionController:
    """Own and govern one TaskState through a bounded declarative workflow."""

    def __init__(
        self,
        state: TaskState,
        workflow: WorkflowName | str | WorkflowDefinition,
        broker: CapabilityBroker,
        event_publisher: ExecutionEventPublisher | None = None,
    ) -> None:
        self.state = state
        self.workflow = (
            workflow if isinstance(workflow, WorkflowDefinition) else get_workflow(workflow)
        )
        self._broker = broker
        self._event_publisher = event_publisher or ExecutionEventPublisher()
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
        self._emit(
            ExecutionEventType.TASK_STARTED,
            ExecutionEventStatus.STARTED,
            "Task execution started.",
        )
        self._emit(
            ExecutionEventType.WORKFLOW_SELECTED,
            ExecutionEventStatus.COMPLETED,
            f"Selected {self.workflow.name.value} workflow.",
        )

    @property
    def execution_events(self) -> tuple[ExecutionEvent, ...]:
        """Expose the ordered, high-level event stream for this task."""

        return self._event_publisher.events

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
                ExecutionEventType.CAPABILITY_REJECTED,
                ExecutionEventStatus.REJECTED,
                rejection_reason,
                capability_id=decision.action,
            )

        if self.state.iteration_count >= self.state.max_iterations:
            self.state.final_status = FinalStatus.FAILED
            self._record_controller_observation(
                "iteration_limit", "Iteration limit exhausted before capability invocation.", decision.action
            )
            limit_event = self._emit(
                ExecutionEventType.LIMIT_EXCEEDED,
                ExecutionEventStatus.FAILED,
                "Iteration limit exhausted; task failed.",
                capability_id=decision.action,
            )
            self._emit_task_failed("Task failed after exhausting its iteration limit.")
            return limit_event

        request = CapabilityRequest(
            capability_name=decision.action,
            inputs=decision.inputs,
            task_id=self.state.session_id,
        )
        self.state.iteration_count += 1
        self.last_action = decision.action
        self._emit(
            ExecutionEventType.CAPABILITY_STARTED,
            ExecutionEventStatus.STARTED,
            f"Invoking {decision.action} through the Capability Broker.",
            capability_id=decision.action,
            request_id=request.request_id,
        )
        if decision.action == "run_code":
            self._emit(
                ExecutionEventType.SANDBOX_STARTED,
                ExecutionEventStatus.STARTED,
                "Sandbox execution started.",
                capability_id=decision.action,
                request_id=request.request_id,
            )
        elif decision.action == "verify_result":
            self._emit(
                ExecutionEventType.VERIFICATION_STARTED,
                ExecutionEventStatus.STARTED,
                "Verification started.",
                capability_id=decision.action,
                request_id=request.request_id,
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
                ExecutionEventType.CAPABILITY_REJECTED,
                ExecutionEventStatus.REJECTED,
                "This workflow does not require human approval.",
            )
        if self.state.final_status != FinalStatus.NOT_FINAL:
            return self._emit(
                ExecutionEventType.CAPABILITY_REJECTED,
                ExecutionEventStatus.REJECTED,
                "Terminal tasks cannot receive approval decisions.",
            )
        if self.state.current_step != "finish" or self.state.verification_status != VerificationStatus.PASSED:
            return self._emit(
                ExecutionEventType.CAPABILITY_REJECTED,
                ExecutionEventStatus.REJECTED,
                "Approval is allowed only after verification passes.",
            )

        if approved:
            self.state.approval_status = ApprovalStatus.APPROVED
            return self._emit(
                ExecutionEventType.APPROVAL_RECORDED,
                ExecutionEventStatus.COMPLETED,
                "Human approval recorded; workflow may finish.",
            )

        self.state.approval_status = ApprovalStatus.REJECTED
        self.state.final_status = FinalStatus.CANCELLED
        self._record_controller_observation(
            "approval_rejected", "Human approval was rejected; task cancelled.", None
        )
        return self._emit(
            ExecutionEventType.APPROVAL_RECORDED,
            ExecutionEventStatus.COMPLETED,
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
        if action == "run_code":
            self._emit(
                ExecutionEventType.SANDBOX_COMPLETED,
                ExecutionEventStatus.COMPLETED,
                "Sandbox execution completed.",
                capability_id=action,
                request_id=result.request_id,
                metadata=self._sandbox_metadata(result),
            )
        elif action == "verify_result":
            self._emit(
                ExecutionEventType.VERIFICATION_COMPLETED,
                ExecutionEventStatus.COMPLETED,
                "Verification completed successfully.",
                capability_id=action,
                request_id=result.request_id,
            )
        completion_event = self._emit(
            ExecutionEventType.CAPABILITY_COMPLETED,
            ExecutionEventStatus.COMPLETED,
            f"Capability {action} completed.",
            capability_id=action,
            request_id=result.request_id,
        )
        if action == "verify_result" and self.workflow.requires_approval:
            self._emit(
                ExecutionEventType.HITL_REQUIRED,
                ExecutionEventStatus.REQUIRES_ACTION,
                "Human approval is required before this workflow can finish.",
            )
        if action == "finish":
            self.state.final_status = FinalStatus.COMPLETED
            return self._emit(
                ExecutionEventType.TASK_COMPLETED,
                ExecutionEventStatus.COMPLETED,
                "Workflow completed.",
                capability_id=action,
                request_id=result.request_id,
            )
        return completion_event

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
            self._emit_capability_completion(action, result, ExecutionEventStatus.FAILED)
            self._emit(
                ExecutionEventType.CAPABILITY_COMPLETED,
                ExecutionEventStatus.FAILED,
                f"Capability {action} failed and retry limit is exhausted.",
                capability_id=action,
                request_id=result.request_id,
            )
            return self._emit_task_failed("Task failed after exhausting retries.", action, result.request_id)

        self.state.retry_count += 1
        self.state.current_step = self.workflow.next_state_on_failure(
            self.state.current_step, action
        )
        self._emit_capability_completion(action, result, ExecutionEventStatus.FAILED)
        return self._emit(
            ExecutionEventType.CAPABILITY_COMPLETED,
            ExecutionEventStatus.FAILED,
            f"Capability {action} failed; corrective action is required.",
            capability_id=action,
            request_id=result.request_id,
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

    def _emit_capability_completion(
        self,
        action: str,
        result: CapabilityResult,
        status: ExecutionEventStatus,
    ) -> None:
        if action == "run_code":
            self._emit(
                ExecutionEventType.SANDBOX_COMPLETED,
                status,
                "Sandbox execution failed." if status == ExecutionEventStatus.FAILED else "Sandbox execution completed.",
                capability_id=action,
                request_id=result.request_id,
                metadata=self._sandbox_metadata(result),
            )
        elif action == "verify_result":
            self._emit(
                ExecutionEventType.VERIFICATION_COMPLETED,
                status,
                "Verification failed." if status == ExecutionEventStatus.FAILED else "Verification completed successfully.",
                capability_id=action,
                request_id=result.request_id,
            )

    @staticmethod
    def _sandbox_metadata(result: CapabilityResult) -> dict[str, object]:
        return {
            key: result.output[key]
            for key in ("exit_code", "timed_out", "error_type")
            if key in result.output
        }

    def _emit_task_failed(
        self,
        summary: str,
        action: str | None = None,
        request_id=None,
    ) -> ExecutionEvent:
        return self._emit(
            ExecutionEventType.TASK_FAILED,
            ExecutionEventStatus.FAILED,
            summary,
            capability_id=action,
            request_id=request_id,
        )

    def _emit(
        self,
        event_type: ExecutionEventType,
        status: ExecutionEventStatus,
        summary: str,
        *,
        capability_id: str | None = None,
        request_id=None,
        metadata: dict[str, object] | None = None,
    ) -> ExecutionEvent:
        return self._event_publisher.publish(
            ExecutionEvent(
                session_id=self.state.session_id,
                task_id=self.state.task_id,
                user_id=self.state.user_id,
                event_type=event_type,
                component="execution_controller",
                status=status,
                summary=summary,
                workflow_id=self.workflow.name.value,
                capability_id=capability_id,
                request_id=request_id,
                metadata=metadata or {},
            )
        )
