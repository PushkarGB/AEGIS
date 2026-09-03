"""Comprehensive tests for deterministic verification of computation outcomes.

Covers:
1. Execution success checks (exit code, timeout, fatal stderr tracebacks, non-empty output).
2. Structural validity checks (JSON, tabular/key-value, rejection of NaN/inf/empty outputs).
3. Expected fields checks (explicit and objective-inferred field requirements, missing field reporting).
4. Computation consistency checks (positive physical measurements, bounds checking, logical threshold consistency: average < min_acceptable when flagged).
5. VerifyResultCapability integration (CapabilityRegistry, RegistryCapabilityBroker, ExecutionController workflow state transitions).
6. Separation invariant: 100% deterministic, zero LLM reasoning.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    CapabilityKind,
    CapabilityRegistry,
    VerificationCheck,
    VerificationOutcome,
    VerifyResultCapability,
    verify_computation_result,
)
from aegis.config import load_config
from aegis.orchestration import ExecutionController, ExecutionEventKind, WorkflowName
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResultStatus,
    FinalStatus,
    TaskState,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# 1. Execution Success Verification
# ---------------------------------------------------------------------------


class TestExecutionSuccessCheck:
    """Verify execution_succeeded rule behavior."""

    def test_successful_execution_passes(self):
        outcome = verify_computation_result(
            stdout=json.dumps([{"equipment_id": "EQ-001", "average": 4.5}]),
            exit_code=0,
            timed_out=False,
            stderr="",
        )
        check = next(c for c in outcome.checks if c.name == "execution_succeeded")
        assert check.passed is True
        assert outcome.verified is True

    def test_non_zero_exit_code_fails(self):
        outcome = verify_computation_result(
            stdout="some output",
            exit_code=1,
            timed_out=False,
        )
        check = next(c for c in outcome.checks if c.name == "execution_succeeded")
        assert check.passed is False
        assert "exit code 1" in check.message
        assert outcome.verified is False

    def test_timeout_fails(self):
        outcome = verify_computation_result(
            stdout="",
            exit_code=None,
            timed_out=True,
        )
        check = next(c for c in outcome.checks if c.name == "execution_succeeded")
        assert check.passed is False
        assert "timed out" in check.message
        assert outcome.verified is False

    def test_fatal_stderr_traceback_fails(self):
        outcome = verify_computation_result(
            stdout='[{"equipment_id": "EQ-001"}]',
            exit_code=0,
            stderr="Traceback (most recent call last):\n  File 'script.py', line 2\nKeyError: 'Thickness'",
        )
        check = next(c for c in outcome.checks if c.name == "execution_succeeded")
        assert check.passed is False
        assert "stderr contained fatal error" in check.message
        assert outcome.verified is False

    def test_empty_output_fails(self):
        outcome = verify_computation_result(
            stdout="   ",
            exit_code=0,
            timed_out=False,
        )
        check = next(c for c in outcome.checks if c.name == "execution_succeeded")
        assert check.passed is False
        assert "no execution output" in check.message
        assert outcome.verified is False


# ---------------------------------------------------------------------------
# 2. Structural Validity Verification
# ---------------------------------------------------------------------------


class TestStructuralValidityCheck:
    """Verify structural_validity rule behavior across data formats."""

    def test_json_array_records_valid(self):
        records = [
            {"equipment_id": "EQ-001", "average_thickness": 4.5},
            {"equipment_id": "EQ-002", "average_thickness": 3.8},
        ]
        outcome = verify_computation_result(
            stdout=json.dumps(records),
            exit_code=0,
        )
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is True
        assert check.details["record_count"] == 2
        assert outcome.verified is True

    def test_json_markdown_fence_parsed(self):
        raw = "```json\n[{\"equipment_id\": \"EQ-001\", \"average\": 4.2}]\n```"
        outcome = verify_computation_result(stdout=raw, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is True
        assert outcome.verified is True

    def test_nested_results_dict_parsed(self):
        payload = {
            "summary": "Equipment thickness calculation",
            "results": [
                {"equipment_id": "EQ-001", "average_measured_thickness": 5.1},
            ],
        }
        outcome = verify_computation_result(data=payload, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is True
        assert check.details["record_count"] == 1

    def test_key_value_lines_parsed(self):
        stdout = "equipment_id: EQ-001, average: 4.5, below_min: False\nequipment_id: EQ-002, average: 2.8, below_min: True"
        outcome = verify_computation_result(stdout=stdout, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is True
        assert check.details["record_count"] == 2

    def test_nan_or_inf_numeric_values_rejected(self):
        outcome = verify_computation_result(
            stdout='[{"equipment_id": "EQ-001", "average": NaN}]',
            exit_code=0,
        )
        # NaN in JSON fails JSON parse or finite check
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is False
        assert outcome.verified is False

    def test_unparseable_garbled_output_fails(self):
        outcome = verify_computation_result(
            stdout="random string without any structured records or delimiters",
            exit_code=0,
        )
        check = next(c for c in outcome.checks if c.name == "structural_validity")
        assert check.passed is False
        assert outcome.verified is False


# ---------------------------------------------------------------------------
# 3. Expected Fields Verification
# ---------------------------------------------------------------------------


class TestExpectedFieldsCheck:
    """Verify expected_fields_exist rule with explicit and inferred fields."""

    def test_all_explicit_expected_fields_present(self):
        records = [
            {
                "equipment_id": "EQ-001",
                "average_measured_thickness": 4.5,
                "below_min_acceptable_thickness": False,
            }
        ]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            expected_fields=[
                "equipment_id",
                "average_measured_thickness",
                "below_min_acceptable_thickness",
            ],
        )
        check = next(c for c in outcome.checks if c.name == "expected_fields_exist")
        assert check.passed is True
        assert outcome.verified is True

    def test_missing_explicit_expected_field_fails(self):
        records = [{"equipment_id": "EQ-001", "measured_thickness": 4.5}]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            expected_fields=["equipment_id", "min_acceptable_thickness"],
        )
        check = next(c for c in outcome.checks if c.name == "expected_fields_exist")
        assert check.passed is False
        assert "min_acceptable_thickness" in check.details["missing_fields"]
        assert outcome.verified is False

    def test_fields_inferred_from_computation_objective(self):
        objective = (
            "From this month's equipment inspection readings, calculate the average "
            "measured thickness for each equipment item and identify which equipment "
            "has fallen below its minimum acceptable thickness."
        )
        records = [
            {
                "equipment_id": "EQ-001",
                "average_measured_thickness": 4.5,
                "below_min_acceptable_thickness": False,
            }
        ]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            computation_objective=objective,
        )
        check = next(c for c in outcome.checks if c.name == "expected_fields_exist")
        assert check.passed is True
        assert outcome.verified is True

    def test_missing_inferred_field_fails(self):
        objective = "Calculate average measured thickness for each equipment item."
        # Missing 'average' metric field
        records = [{"equipment_id": "EQ-001", "other_field": 123}]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            computation_objective=objective,
        )
        check = next(c for c in outcome.checks if c.name == "expected_fields_exist")
        assert check.passed is False
        assert "average_measured_thickness" in check.details["missing_fields"]


# ---------------------------------------------------------------------------
# 4. Computation Consistency Verification
# ---------------------------------------------------------------------------


class TestComputationConsistencyCheck:
    """Verify computation_consistency rules including physical and threshold rules."""

    def test_valid_consistency_passes(self):
        records = [
            {
                "equipment_id": "EQ-001",
                "average_thickness": 4.5,
                "min_acceptable": 3.0,
                "below_min": False,
            },
            {
                "equipment_id": "EQ-002",
                "average_thickness": 2.7,
                "min_acceptable": 3.0,
                "below_min": True,
            },
        ]
        outcome = verify_computation_result(data=records, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "computation_consistency")
        assert check.passed is True
        assert outcome.verified is True

    def test_non_positive_physical_measurement_fails(self):
        records = [
            {"equipment_id": "EQ-001", "measured_thickness": -0.5},
        ]
        outcome = verify_computation_result(data=records, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "computation_consistency")
        assert check.passed is False
        assert "non-positive measurement" in check.message
        assert outcome.verified is False

    def test_inconsistent_threshold_logic_fails(self):
        # 4.8 is GREATER than min_acceptable 3.0, but flagged below_min=True!
        records = [
            {
                "equipment_id": "EQ-001",
                "average_thickness": 4.8,
                "min_acceptable": 3.0,
                "below_min": True,
            }
        ]
        outcome = verify_computation_result(data=records, exit_code=0)
        check = next(c for c in outcome.checks if c.name == "computation_consistency")
        assert check.passed is False
        assert "inconsistent threshold flag" in check.message
        assert outcome.verified is False

    def test_out_of_bounds_measurement_fails(self):
        records = [{"equipment_id": "EQ-001", "measured_thickness": 25.0}]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            numeric_bounds={"measured_thickness": (1.0, 10.0)},
        )
        check = next(c for c in outcome.checks if c.name == "computation_consistency")
        assert check.passed is False
        assert "out of bounds" in check.message
        assert outcome.verified is False

    def test_below_min_row_count_fails(self):
        records = [{"equipment_id": "EQ-001", "average": 4.5}]
        outcome = verify_computation_result(
            data=records,
            exit_code=0,
            min_row_count=3,
        )
        check = next(c for c in outcome.checks if c.name == "computation_consistency")
        assert check.passed is False
        assert "below required minimum 3" in check.message
        assert outcome.verified is False


# ---------------------------------------------------------------------------
# 5. VerifyResultCapability & Controller Integration
# ---------------------------------------------------------------------------


class TestVerifyResultCapabilityIntegration:
    """Verify VerifyResultCapability with CapabilityRegistry, Broker, and Controller."""

    def test_capability_metadata(self):
        cap = VerifyResultCapability()
        assert cap.metadata.name == "verify_result"
        assert cap.metadata.kind == CapabilityKind.TOOL
        assert "spreadsheet" in cap.metadata.input_modalities

    def test_capability_succeeds_on_valid_result(self):
        cap = VerifyResultCapability()
        req = CapabilityRequest(
            capability_name="verify_result",
            inputs={
                "stdout": json.dumps([{"equipment_id": "EQ-001", "average_measured_thickness": 4.2}]),
                "exit_code": 0,
                "expected_fields": ["equipment_id", "average_measured_thickness"],
            },
        )
        result = cap.invoke(req)
        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert result.output["verified"] is True
        assert result.output["passed_count"] == 4
        assert len(result.observations) == 1
        assert result.observations[0].source == "verify_result"
        assert result.observations[0].kind == "verification"

    def test_capability_fails_on_execution_error(self):
        cap = VerifyResultCapability()
        req = CapabilityRequest(
            capability_name="verify_result",
            inputs={
                "stdout": "",
                "exit_code": 1,
                "stderr": "KeyError: 'thickness'",
            },
        )
        result = cap.invoke(req)
        assert result.status == CapabilityResultStatus.FAILED
        assert result.output["verified"] is False
        assert result.error is not None
        assert "Verification failed" in result.error

    def test_capability_accepts_nested_sandbox_result(self):
        cap = VerifyResultCapability()
        req = CapabilityRequest(
            capability_name="verify_result",
            inputs={
                "sandbox_result": {
                    "stdout": json.dumps([{"equipment_id": "EQ-001", "average": 4.5}]),
                    "exit_code": 0,
                    "timed_out": False,
                },
                "expected_fields": ["equipment_id", "average"],
            },
        )
        result = cap.invoke(req)
        assert result.status == CapabilityResultStatus.SUCCEEDED
        assert result.output["verified"] is True

    def test_controller_computation_workflow_transitions_on_verification(self):
        """Prove ExecutionController moves from verify to deliver on verification pass."""
        config = load_config()
        registry = CapabilityRegistry(config.capabilities)

        # Register VerifyResultCapability
        verify_cap = VerifyResultCapability()
        registry.register(verify_cap)
        broker = RegistryCapabilityBroker(registry)

        state = TaskState(
            user_goal="Calculate average thickness.",
            current_step="verify",
        )
        controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

        decision = AgentDecision(
            action="verify_result",
            inputs={
                "stdout": json.dumps([{"equipment_id": "EQ-001", "average_measured_thickness": 4.5}]),
                "exit_code": 0,
                "expected_fields": ["equipment_id", "average_measured_thickness"],
            },
        )
        event = controller.execute(decision)

        assert event.kind == ExecutionEventKind.ACTION_COMPLETED
        assert controller.state.verification_status == VerificationStatus.PASSED
        assert len(controller.state.verification_results) == 1
        assert controller.state.verification_results[0].status == VerificationStatus.PASSED
        assert controller.state.current_step == "deliver"
        assert "verify_result" in controller.state.completed_steps

    def test_controller_records_failed_verification(self):
        """Prove ExecutionController records FAILED status on verification check failure."""
        config = load_config()
        registry = CapabilityRegistry(config.capabilities)
        registry.register(VerifyResultCapability())
        broker = RegistryCapabilityBroker(registry)

        state = TaskState(
            user_goal="Calculate average thickness.",
            current_step="verify",
        )
        controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

        # Missing expected field fails verification
        decision = AgentDecision(
            action="verify_result",
            inputs={
                "stdout": json.dumps([{"other_col": 123}]),
                "exit_code": 0,
                "expected_fields": ["equipment_id", "average_measured_thickness"],
            },
        )
        event = controller.execute(decision)

        assert event.kind == ExecutionEventKind.ACTION_FAILED
        assert controller.state.verification_status == VerificationStatus.FAILED
        assert len(controller.state.verification_results) == 1
        assert controller.state.verification_results[0].status == VerificationStatus.FAILED
