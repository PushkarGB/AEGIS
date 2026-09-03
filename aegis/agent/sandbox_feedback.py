"""Connect Controller-owned sandbox observations back to the Agent.

The Agent only reasons and proposes. The Controller still records failures,
enforces retry/iteration limits, and invokes capabilities through the Broker.

This module implements the bounded computation recovery loop:

    ACT → OBSERVE ERROR → REASON → CORRECT → ACT
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.agent.runtime import AgentRuntime
from aegis.agent.schemas import (
    AgentDirective,
    ObservationDecision,
    ObservationReasoningRequest,
    PreviousExecutionContext,
)
from aegis.orchestration.controller import ExecutionController, ExecutionEvent
from aegis.schemas import (
    AgentDecision,
    CapabilityResultStatus,
    FinalStatus,
    JsonObject,
    Observation,
)
from aegis.skills.computation import (
    ComputationContext,
    build_code_generation_prompt,
    build_retry_context,
    parse_execution_observation,
    prepare_generate_code_inputs,
    prepare_run_code_inputs,
)


@dataclass(frozen=True, slots=True)
class SandboxRecoveryResult:
    """Trace of one sandbox failure recovery cycle."""

    error_observation: Observation
    reason_decision: ObservationDecision
    correction_event: ExecutionEvent | None = None
    rerun_decision: ObservationDecision | None = None
    rerun_event: ExecutionEvent | None = None


class SandboxObservationLoop:
    """Feed sandbox observations to the Agent and apply bounded Controller actions."""

    def __init__(self, agent: AgentRuntime, controller: ExecutionController) -> None:
        self._agent = agent
        self._controller = controller

    @property
    def controller(self) -> ExecutionController:
        return self._controller

    def build_reasoning_request(self) -> ObservationReasoningRequest:
        """Package Controller state into a structured Agent observation request."""

        last_result = self._controller.last_capability_result
        last_status = last_result.status if last_result is not None else None
        last_event = (
            self._controller.execution_events[-1]
            if self._controller.execution_events
            else None
        )
        return ObservationReasoningRequest(
            task_state=self._controller.state,
            latest_observation=self._controller.observation_for_agent(),
            previous_context=PreviousExecutionContext(
                last_action=self._controller.last_action,
                last_result_status=last_status,
                allowed_next_actions=list(self._controller.allowed_next_actions()),
                controller_summary=last_event.summary if last_event is not None else None,
            ),
        )

    def reason(self) -> ObservationDecision:
        """Ask the Agent to interpret the latest Controller-owned observation."""

        return self._agent.reason_about_observation(self.build_reasoning_request())

    def apply(
        self,
        decision: ObservationDecision,
        *,
        inputs_overlay: JsonObject | None = None,
    ) -> ExecutionEvent:
        """Execute one Agent proposal through the Controller. Never invoke tools directly."""

        if decision.proposed_action is None:
            raise ValueError("Observation decision has no proposed action for the Controller.")
        action = _overlay_inputs(decision.proposed_action, inputs_overlay)
        return self._controller.execute(action)

    def recover_from_run_code_failure(
        self,
        context: ComputationContext,
        previous_code: str,
        *,
        data_file_path: str | None = None,
    ) -> SandboxRecoveryResult:
        """Run ACT(error) → OBSERVE ERROR → REASON → CORRECT → ACT.

        Assumes the Controller has already executed ``run_code`` and recorded a
        failure. The Agent may propose ``generate_code``; corrected code is then
        executed again via ``run_code``. Retry limits remain Controller-owned.
        """

        if self._controller.last_action != "run_code":
            raise ValueError("Sandbox recovery requires a prior run_code invocation.")
        last_result = self._controller.last_capability_result
        if last_result is None or last_result.status == CapabilityResultStatus.SUCCEEDED:
            raise ValueError("Sandbox recovery requires a failed run_code result.")

        error_observation = self._controller.observation_for_agent()
        outcome = parse_execution_observation(last_result)
        reason_decision = self.reason()

        if (
            reason_decision.directive != AgentDirective.RETRY_CORRECT
            or reason_decision.proposed_action is None
            or reason_decision.proposed_action.action != "generate_code"
        ):
            return SandboxRecoveryResult(
                error_observation=error_observation,
                reason_decision=reason_decision,
            )

        retry_context = build_retry_context(context, outcome, previous_code)
        correction_inputs = prepare_generate_code_inputs(
            build_code_generation_prompt(retry_context)
        )
        correction_event = self.apply(reason_decision, inputs_overlay=correction_inputs)

        if self._controller.state.final_status != FinalStatus.NOT_FINAL:
            return SandboxRecoveryResult(
                error_observation=error_observation,
                reason_decision=reason_decision,
                correction_event=correction_event,
            )

        generated = self._controller.last_capability_result
        if generated is None or generated.status != CapabilityResultStatus.SUCCEEDED:
            return SandboxRecoveryResult(
                error_observation=error_observation,
                reason_decision=reason_decision,
                correction_event=correction_event,
            )
        generated_code = generated.output.get("code")
        if not isinstance(generated_code, str) or not generated_code.strip():
            return SandboxRecoveryResult(
                error_observation=error_observation,
                reason_decision=reason_decision,
                correction_event=correction_event,
            )

        rerun_decision = self.reason()
        if (
            rerun_decision.proposed_action is None
            or rerun_decision.proposed_action.action != "run_code"
        ):
            return SandboxRecoveryResult(
                error_observation=error_observation,
                reason_decision=reason_decision,
                correction_event=correction_event,
                rerun_decision=rerun_decision,
            )

        rerun_inputs = prepare_run_code_inputs(
            generated_code, data_file_path or context.file_path
        )
        rerun_event = self.apply(rerun_decision, inputs_overlay=rerun_inputs)
        return SandboxRecoveryResult(
            error_observation=error_observation,
            reason_decision=reason_decision,
            correction_event=correction_event,
            rerun_decision=rerun_decision,
            rerun_event=rerun_event,
        )


def _overlay_inputs(decision: AgentDecision, overlay: JsonObject | None) -> AgentDecision:
    if not overlay:
        return decision
    merged: JsonObject = dict(decision.inputs)
    merged.update(overlay)
    return decision.model_copy(update={"inputs": merged})
