"""Structured request/response schemas for the AEGIS Agent Runtime."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from aegis.orchestration import WorkflowName
from aegis.schemas import AgentDecision, CapabilityResultStatus, Observation, TaskState

CAPABILITY_PATTERN = r"^[a-z][a-z0-9_]*$"

JsonObject = dict[str, JsonValue]


def _ensure_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def workflow_for_intent(intent: "AgentIntent") -> WorkflowName:
    """Map one prototype intent to its Controller workflow."""

    mapping = {
        AgentIntent.COMPUTATION: WorkflowName.COMPUTATION,
        AgentIntent.DOCUMENT_DRAFTING: WorkflowName.SCANNED_DOCUMENT_APPROVAL,
        AgentIntent.MULTIMODAL_ANALYSIS: WorkflowName.MULTIMODAL_ANALYSIS,
    }
    return mapping[intent]


def validate_intent_modality_pair(intent: "AgentIntent", modality: "InputModality") -> None:
    """Enforce the prototype's currently supported intent/modality combinations."""

    allowed_pairs = {
        (AgentIntent.COMPUTATION, InputModality.SPREADSHEET),
        (AgentIntent.DOCUMENT_DRAFTING, InputModality.SCANNED_DOCUMENT),
        (AgentIntent.MULTIMODAL_ANALYSIS, InputModality.IMAGE),
    }
    if (intent, modality) not in allowed_pairs:
        raise ValueError(
            f"Unsupported intent/modality combination: {intent.value}/{modality.value}"
        )


class AgentSchema(BaseModel):
    """Strict base model for Agent Runtime contracts."""

    model_config = ConfigDict(extra="forbid")


class AgentIntent(StrEnum):
    """Supported high-level intents for the prototype Agent."""

    COMPUTATION = "computation"
    DOCUMENT_DRAFTING = "document_drafting"
    MULTIMODAL_ANALYSIS = "multimodal_analysis"


class InputModality(StrEnum):
    """Prototype input modalities the Agent can classify explicitly."""

    SPREADSHEET = "spreadsheet"
    SCANNED_DOCUMENT = "scanned_document"
    IMAGE = "image"


class AgentDirective(StrEnum):
    """Structured Controller-facing decision categories without chain-of-thought."""

    CONTINUE = "continue"
    RETRY_CORRECT = "retry_correct"
    VERIFY = "verify"
    FINISH = "finish"
    REQUEST_APPROVAL = "request_approval"


class AttachmentDescriptor(AgentSchema):
    """Lightweight attachment metadata for semantic request understanding."""

    name: str = Field(min_length=1)
    media_type: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)


class IntentAnalysisRequest(AgentSchema):
    """Structured input for Agent intent and modality detection."""

    user_goal: str = Field(min_length=1)
    attachments: list[AttachmentDescriptor] = Field(default_factory=list)


class IntentAnalysisResult(AgentSchema):
    """Structured Agent understanding suitable for Controller workflow selection."""

    intent: AgentIntent
    modality: InputModality
    workflow: WorkflowName
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_consistency(self) -> "IntentAnalysisResult":
        validate_intent_modality_pair(self.intent, self.modality)
        expected_workflow = workflow_for_intent(self.intent)
        if self.workflow != expected_workflow:
            raise ValueError(
                f"workflow must be '{expected_workflow.value}' for intent '{self.intent.value}'"
            )
        return self


class CapabilityPlanStep(AgentSchema):
    """One bounded capability request proposed by the Agent."""

    capability_name: str = Field(min_length=1, pattern=CAPABILITY_PATTERN)
    purpose: str = Field(min_length=1)
    inputs: JsonObject = Field(default_factory=dict)


class PlanGenerationRequest(AgentSchema):
    """Structured input for bounded Controller-facing plan generation."""

    user_goal: str = Field(min_length=1)
    intent: AgentIntent
    modality: InputModality
    available_capabilities: list[str] = Field(min_length=1)

    @field_validator("available_capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "available_capabilities")

    @model_validator(mode="after")
    def validate_pair(self) -> "PlanGenerationRequest":
        validate_intent_modality_pair(self.intent, self.modality)
        return self


class PlanProposal(AgentSchema):
    """A bounded sequence of capability requests for Controller validation."""

    intent: AgentIntent
    modality: InputModality
    workflow: WorkflowName
    steps: list[CapabilityPlanStep] = Field(min_length=1)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_consistency(self) -> "PlanProposal":
        validate_intent_modality_pair(self.intent, self.modality)
        expected_workflow = workflow_for_intent(self.intent)
        if self.workflow != expected_workflow:
            raise ValueError(
                f"workflow must be '{expected_workflow.value}' for intent '{self.intent.value}'"
            )
        if len({step.capability_name for step in self.steps}) != len(self.steps):
            raise ValueError("steps must not repeat capability_name entries")
        if any(
            step.capability_name == "finish" for step in self.steps[:-1]
        ):
            raise ValueError("finish may appear only as the final planned step")
        return self


class PreviousExecutionContext(AgentSchema):
    """Bounded execution context exposed back to the Agent for reasoning."""

    last_action: str | None = Field(default=None, min_length=1, pattern=CAPABILITY_PATTERN)
    last_result_status: CapabilityResultStatus | None = None
    allowed_next_actions: list[str] = Field(default_factory=list)
    controller_summary: str | None = Field(default=None, min_length=1)

    @field_validator("allowed_next_actions")
    @classmethod
    def validate_allowed_next_actions(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "allowed_next_actions")


class ObservationReasoningRequest(AgentSchema):
    """Structured Agent input for observation-driven next-step reasoning."""

    task_state: TaskState
    latest_observation: Observation
    previous_context: PreviousExecutionContext = Field(
        default_factory=PreviousExecutionContext
    )


class ObservationDecision(AgentSchema):
    """A structured Controller-facing decision with an optional proposed action."""

    directive: AgentDirective
    proposed_action: AgentDecision | None = None
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_directive(self) -> "ObservationDecision":
        if self.directive == AgentDirective.REQUEST_APPROVAL:
            if self.proposed_action is not None:
                raise ValueError(
                    "request_approval decisions must not include a proposed_action"
                )
            return self

        if self.proposed_action is None:
            raise ValueError(
                f"{self.directive.value} decisions require a proposed_action"
            )

        if self.directive == AgentDirective.VERIFY:
            if self.proposed_action.action != "verify_result":
                raise ValueError("verify decisions must propose the verify_result action")
            if self.proposed_action.done:
                raise ValueError("verify decisions must not mark the task done")
        elif self.directive == AgentDirective.FINISH:
            if self.proposed_action.action != "finish" or not self.proposed_action.done:
                raise ValueError(
                    "finish decisions must propose action='finish' with done=true"
                )
        else:
            if self.proposed_action.action == "finish" or self.proposed_action.done:
                raise ValueError(
                    "continue and retry_correct decisions must not prematurely finish the task"
                )

        return self
