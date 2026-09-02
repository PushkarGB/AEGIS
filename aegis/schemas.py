"""Provider-neutral shared data contracts for the AEGIS prototype."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

CAPABILITY_PATTERN = r"^[a-z][a-z0-9_]*$"

JsonObject = dict[str, JsonValue]
NonEmptyText = Annotated[str, Field(min_length=1)]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_unique(values: list[object], field_name: str) -> list[object]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class CapabilityResultStatus(StrEnum):
    """Outcome of a bounded capability invocation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    """Current verification state for a task or individual result."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    """Human approval state for workflows that require it."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FinalStatus(StrEnum):
    """Terminal status owned by the future Execution Controller."""

    NOT_FINAL = "not_final"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SharedSchema(BaseModel):
    """Strict base model for serializable runtime contracts."""

    model_config = ConfigDict(extra="forbid")


class CapabilityRequest(SharedSchema):
    """A controller-governed request for one registered capability."""

    request_id: UUID = Field(default_factory=uuid4)
    capability_name: str = Field(min_length=1, pattern=CAPABILITY_PATTERN)
    inputs: JsonObject = Field(default_factory=dict)
    task_id: UUID | None = None
    requested_at: datetime = Field(default_factory=_utc_now)

    @field_validator("requested_at")
    @classmethod
    def validate_requested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("requested_at must include timezone information")
        return value


class AgentDecision(SharedSchema):
    """A structured Agent proposal; it is not permission to execute."""

    decision_id: UUID = Field(default_factory=uuid4)
    action: str = Field(min_length=1, pattern=CAPABILITY_PATTERN)
    inputs: JsonObject = Field(default_factory=dict)
    done: bool = False
    summary: str | None = Field(default=None, min_length=1)
    decided_at: datetime = Field(default_factory=_utc_now)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("decided_at must include timezone information")
        return value


class Observation(SharedSchema):
    """A normalized record of information observed during a task."""

    observation_id: UUID = Field(default_factory=uuid4)
    source: NonEmptyText
    kind: NonEmptyText
    summary: NonEmptyText
    data: JsonObject = Field(default_factory=dict)
    request_id: UUID | None = None
    observed_at: datetime = Field(default_factory=_utc_now)

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include timezone information")
        return value


class Artifact(SharedSchema):
    """A local task output reference, independent of its generating implementation."""

    artifact_id: UUID = Field(default_factory=uuid4)
    name: NonEmptyText
    media_type: NonEmptyText
    location: NonEmptyText
    description: str | None = Field(default=None, min_length=1)
    source_request_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include timezone information")
        return value


class CapabilityResult(SharedSchema):
    """Structured outcome returned from a bounded capability."""

    request_id: UUID
    status: CapabilityResultStatus
    output: JsonObject = Field(default_factory=dict)
    error: str | None = Field(default=None, min_length=1)
    observations: list[Observation] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=_utc_now)

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("completed_at must include timezone information")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> "CapabilityResult":
        if self.status == CapabilityResultStatus.SUCCEEDED and self.error is not None:
            raise ValueError("successful capability results must not contain an error")
        if self.status != CapabilityResultStatus.SUCCEEDED and self.error is None:
            raise ValueError("failed or rejected capability results require an error")
        return self


class VerificationResult(SharedSchema):
    """A provider-neutral record of a verification check and its outcome."""

    verification_id: UUID = Field(default_factory=uuid4)
    status: VerificationStatus
    verifier: NonEmptyText
    summary: NonEmptyText
    details: JsonObject = Field(default_factory=dict)
    artifact_ids: list[UUID] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=_utc_now)

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, value: list[UUID]) -> list[UUID]:
        return _ensure_unique(value, "artifact_ids")  # type: ignore[return-value]

    @field_validator("verified_at")
    @classmethod
    def validate_verified_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("verified_at must include timezone information")
        return value


class TaskState(SharedSchema):
    """Controller-owned task record without Controller execution behavior."""

    session_id: UUID = Field(default_factory=uuid4)
    user_goal: NonEmptyText
    attachments: list[str] = Field(default_factory=list)
    intent: str | None = Field(default=None, min_length=1)
    modality: str | None = Field(default=None, min_length=1)
    selected_skill: str | None = Field(default=None, min_length=1)
    plan: list[str] = Field(default_factory=list)
    current_step: str | None = Field(default=None, min_length=1)
    completed_steps: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    generated_artifacts: list[Artifact] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.PENDING
    verification_results: list[VerificationResult] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    iteration_count: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=6, ge=1, le=50)
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    final_status: FinalStatus = FinalStatus.NOT_FINAL

    @field_validator("attachments", "completed_steps")
    @classmethod
    def validate_unique_strings(cls, value: list[str], info: object) -> list[str]:
        return _ensure_unique(value, getattr(info, "field_name", "values"))  # type: ignore[return-value]

    @field_validator("observations")
    @classmethod
    def validate_unique_observations(cls, value: list[Observation]) -> list[Observation]:
        _ensure_unique([observation.observation_id for observation in value], "observations")
        return value

    @field_validator("generated_artifacts")
    @classmethod
    def validate_unique_artifacts(cls, value: list[Artifact]) -> list[Artifact]:
        _ensure_unique([artifact.artifact_id for artifact in value], "generated_artifacts")
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> "TaskState":
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        if self.iteration_count > self.max_iterations:
            raise ValueError("iteration_count cannot exceed max_iterations")
        return self
