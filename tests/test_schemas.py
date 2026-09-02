"""Validation and serialization tests for provider-neutral shared schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegis.schemas import (
    AgentDecision,
    ApprovalStatus,
    Artifact,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    FinalStatus,
    Observation,
    TaskState,
    VerificationResult,
    VerificationStatus,
)


def test_task_state_serializes_shared_runtime_records():
    session_id = uuid4()
    request = CapabilityRequest(
        capability_name="inspect_spreadsheet",
        inputs={"workbook": "uploads/readings.xlsx"},
    )
    observation = Observation(
        source="inspect_spreadsheet",
        kind="workbook_schema",
        summary="Found one equipment readings worksheet.",
        request_id=request.request_id,
    )
    artifact = Artifact(
        name="calculation.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        location="artifacts/calculation.xlsx",
        source_request_id=request.request_id,
    )
    verification = VerificationResult(
        status=VerificationStatus.PASSED,
        verifier="deterministic_calculation_check",
        summary="Calculated values match the expected totals.",
        artifact_ids=[artifact.artifact_id],
    )
    state = TaskState(
        session_id=session_id,
        user_goal="Calculate equipment thickness averages.",
        attachments=["uploads/readings.xlsx"],
        intent="computation",
        modality="spreadsheet",
        selected_skill="equipment_thickness_calculation",
        plan=["Inspect workbook", "Calculate results"],
        current_step="Calculate results",
        completed_steps=["Inspect workbook"],
        observations=[observation],
        generated_artifacts=[artifact],
        verification_status=VerificationStatus.PASSED,
        verification_results=[verification],
        retry_count=1,
        max_retries=2,
        iteration_count=2,
        max_iterations=6,
        approval_status=ApprovalStatus.NOT_REQUIRED,
        final_status=FinalStatus.COMPLETED,
    )

    serialized = state.model_dump(mode="json")

    assert serialized["session_id"] == str(session_id)
    assert serialized["user_goal"] == "Calculate equipment thickness averages."
    assert serialized["attachments"] == ["uploads/readings.xlsx"]
    assert serialized["intent"] == "computation"
    assert serialized["modality"] == "spreadsheet"
    assert serialized["selected_skill"] == "equipment_thickness_calculation"
    assert serialized["plan"] == ["Inspect workbook", "Calculate results"]
    assert serialized["current_step"] == "Calculate results"
    assert serialized["completed_steps"] == ["Inspect workbook"]
    assert serialized["observations"][0]["request_id"] == str(request.request_id)
    assert serialized["generated_artifacts"][0]["artifact_id"] == str(artifact.artifact_id)
    assert serialized["verification_status"] == "passed"
    assert serialized["verification_results"][0]["status"] == "passed"
    assert serialized["retry_count"] == 1
    assert serialized["iteration_count"] == 2
    assert serialized["approval_status"] == "not_required"
    assert serialized["final_status"] == "completed"
    assert TaskState.model_validate(serialized) == state


def test_agent_decision_is_structured_and_serializable():
    decision = AgentDecision(
        action="generate_code",
        inputs={"computation": "average thickness by equipment"},
        summary="Generate a calculation program for the inspected workbook.",
    )

    assert decision.done is False
    assert decision.model_dump(mode="json")["action"] == "generate_code"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (CapabilityResultStatus.SUCCEEDED, "unexpected error"),
        (CapabilityResultStatus.FAILED, None),
        (CapabilityResultStatus.REJECTED, None),
    ],
)
def test_capability_result_enforces_consistent_outcomes(status, error):
    with pytest.raises(ValidationError):
        CapabilityResult(
            request_id=uuid4(),
            status=status,
            error=error,
        )


def test_task_state_rejects_exhausted_limits_and_duplicate_records():
    observation = Observation(
        source="sandbox",
        kind="execution",
        summary="Code completed.",
    )

    with pytest.raises(ValidationError):
        TaskState(
            user_goal="Run a calculation.",
            retry_count=3,
            max_retries=2,
        )

    with pytest.raises(ValidationError):
        TaskState(
            user_goal="Run a calculation.",
            observations=[observation, observation],
        )


def test_shared_schemas_reject_unknown_fields_and_naive_timestamps():
    with pytest.raises(ValidationError):
        CapabilityRequest.model_validate(
            {
                "capability_name": "finish",
                "requested_at": "2026-09-02T12:00:00",
                "unexpected": True,
            }
        )
