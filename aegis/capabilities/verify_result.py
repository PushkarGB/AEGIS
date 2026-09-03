"""Deterministic verification for computation results in Workflow B.

Verifies:
1. Execution succeeded (exit code 0, no timeout, no fatal stderr tracebacks).
2. Result is structurally valid (parseable data/JSON, finite non-NaN numbers).
3. Expected result fields exist (required columns/fields present).
4. Output is consistent with requested computation (positive physical values,
   bounds consistency, logical threshold consistency: average < min_acceptable
   when flagged as below minimum).

All verification rules are strictly deterministic; no LLMs or external APIs.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    JsonObject,
    Observation,
)


class VerificationCheck(BaseModel):
    """Result of an individual deterministic verification check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    passed: bool
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationOutcome(BaseModel):
    """Overall outcome of deterministic verification rules applied to outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verified: bool
    checks: list[VerificationCheck] = Field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    summary: str = Field(min_length=1)
    data: Any = None


def _normalize_key(key: str) -> str:
    """Normalize a dictionary or column key for robust matching."""
    return re.sub(r"[^a-z0-9]", "_", key.strip().lower()).strip("_")


def _try_parse_json(text: str) -> Any:
    """Attempt to parse text as JSON, including markdown code fences."""
    stripped = text.strip()
    if not stripped:
        return None

    # Direct JSON parse
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    # Match markdown code block ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Match outer brackets or braces
    for start_char, end_char in (("[", "]"), ("{", "}")):
        start_idx = stripped.find(start_char)
        end_idx = stripped.rfind(end_char)
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                return json.loads(stripped[start_idx : end_idx + 1])
            except (json.JSONDecodeError, ValueError):
                pass

    return None


def _parse_key_value_lines(text: str) -> list[dict[str, Any]] | None:
    """Attempt to parse structured key-value text or records line by line."""
    records: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None

    kv_pattern = re.compile(r"([a-zA-Z0-9_\-\s]+)[:=]([^,;\n]+)")

    for line in lines:
        matches = kv_pattern.findall(line)
        if matches:
            record: dict[str, Any] = {}
            for raw_k, raw_v in matches:
                k = _normalize_key(raw_k)
                v = raw_v.strip().strip("'\"")
                # Try converting to numeric or boolean
                if v.lower() == "true":
                    record[k] = True
                elif v.lower() == "false":
                    record[k] = False
                else:
                    try:
                        record[k] = int(v) if v.isdigit() else float(v)
                    except ValueError:
                        record[k] = v
            if record:
                records.append(record)

    return records if records else None


def _extract_records(data: Any, stdout: str | None) -> list[dict[str, Any]]:
    """Extract a list of record dictionaries from provided data or stdout."""
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return list(data)
        if data and all(isinstance(item, (int, float, str)) for item in data):
            return [{"value": item} for item in data]
    elif isinstance(data, dict):
        for candidate_key in ("readings", "records", "results", "equipment", "data", "items"):
            candidate = data.get(candidate_key)
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                return list(candidate)
        return [dict(data)]

    if stdout and stdout.strip():
        parsed_json = _try_parse_json(stdout)
        if parsed_json is not None:
            return _extract_records(parsed_json, None)

        kv_records = _parse_key_value_lines(stdout)
        if kv_records:
            return kv_records

    return []


def _infer_expected_fields(objective: str | None) -> list[str]:
    """Deterministically infer expected result fields from computation objective."""
    if not objective:
        return []
    obj_lower = objective.lower()
    inferred: list[str] = []

    if any(term in obj_lower for term in ("equipment", "item", "unit", "tag", "asset")):
        inferred.append("equipment_id")
    if any(term in obj_lower for term in ("average", "mean", "avg")):
        inferred.append("average_measured_thickness")
    if any(term in obj_lower for term in ("below", "minimum", "acceptable", "threshold")):
        inferred.append("below_min_acceptable_thickness")

    return inferred


def verify_computation_result(
    *,
    stdout: str | None = None,
    stderr: str | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
    data: Any = None,
    expected_fields: list[str] | None = None,
    computation_objective: str | None = None,
    min_row_count: int | None = None,
    numeric_bounds: dict[str, tuple[float, float]] | None = None,
    context: dict[str, Any] | None = None,
) -> VerificationOutcome:
    """Run deterministic verification checks on computation results.

    Returns a structured VerificationOutcome indicating overall pass/fail,
    detailed checks, and summary message.
    """
    checks: list[VerificationCheck] = []

    # -------------------------------------------------------------------------
    # 1. Execution Succeeded Check
    # -------------------------------------------------------------------------
    exec_passed = True
    exec_reasons: list[str] = []

    if timed_out:
        exec_passed = False
        exec_reasons.append("execution timed out")

    if exit_code is not None and exit_code != 0:
        exec_passed = False
        exec_reasons.append(f"exit code {exit_code}")

    if stderr:
        fatal_signatures = (
            "Traceback (most recent call last)",
            "SyntaxError:",
            "ZeroDivisionError:",
            "KeyError:",
            "NameError:",
            "TypeError:",
            "AttributeError:",
            "IndexError:",
            "ValueError:",
            "FileNotFoundError:",
        )
        for sig in fatal_signatures:
            if sig in stderr:
                exec_passed = False
                exec_reasons.append(f"stderr contained fatal error: {sig.strip(':')}")
                break

    has_output = bool((stdout and stdout.strip()) or (data is not None))
    if not has_output:
        exec_passed = False
        exec_reasons.append("no execution output or data provided")

    checks.append(
        VerificationCheck(
            name="execution_succeeded",
            passed=exec_passed,
            message=(
                "Execution succeeded with non-empty output."
                if exec_passed
                else f"Execution failed: {', '.join(exec_reasons)}."
            ),
            details={
                "exit_code": exit_code,
                "timed_out": timed_out,
                "has_stdout": bool(stdout and stdout.strip()),
                "has_data": data is not None,
                "reasons": exec_reasons,
            },
        )
    )

    # -------------------------------------------------------------------------
    # 2. Structural Validity Check
    # -------------------------------------------------------------------------
    records = _extract_records(data, stdout)
    struct_passed = True
    struct_reasons: list[str] = []

    if not records:
        struct_passed = False
        struct_reasons.append("output could not be parsed into structured records or key-value data")
    else:
        # Validate finite numeric values and record content
        for idx, rec in enumerate(records):
            for k, val in rec.items():
                if not isinstance(val, bool) and isinstance(val, (int, float)):
                    if math.isnan(val) or math.isinf(val):
                        struct_passed = False
                        struct_reasons.append(f"record {idx} field '{k}' has invalid non-finite value: {val}")
                        break

    checks.append(
        VerificationCheck(
            name="structural_validity",
            passed=struct_passed,
            message=(
                f"Result is structurally valid ({len(records)} record(s) parsed, valid data types)."
                if struct_passed
                else f"Result is structurally invalid: {', '.join(struct_reasons)}."
            ),
            details={"record_count": len(records), "reasons": struct_reasons},
        )
    )

    # -------------------------------------------------------------------------
    # 3. Expected Result Fields Check
    # -------------------------------------------------------------------------
    fields_to_check = list(expected_fields or [])
    if not fields_to_check and computation_objective:
        fields_to_check = _infer_expected_fields(computation_objective)

    fields_passed = True
    missing_fields: list[str] = []

    if fields_to_check and records:
        normalized_record_keys: set[str] = set()
        for rec in records:
            for k in rec.keys():
                normalized_record_keys.add(_normalize_key(k))

        for field_name in fields_to_check:
            norm_target = _normalize_key(field_name)
            # Match directly or by keyword containment (e.g. 'average' in 'average_measured_thickness')
            matched = any(
                norm_target == rk or norm_target in rk or rk in norm_target
                for rk in normalized_record_keys
            )
            if not matched:
                missing_fields.append(field_name)

        if missing_fields:
            fields_passed = False

    checks.append(
        VerificationCheck(
            name="expected_fields_exist",
            passed=fields_passed,
            message=(
                f"All expected fields present: {', '.join(fields_to_check)}."
                if fields_passed and fields_to_check
                else (
                    f"Missing expected fields: {', '.join(missing_fields)}."
                    if missing_fields
                    else "No specific fields required or fields verified."
                )
            ),
            details={"expected_fields": fields_to_check, "missing_fields": missing_fields},
        )
    )

    # -------------------------------------------------------------------------
    # 4. Computation Consistency Check
    # -------------------------------------------------------------------------
    consistency_passed = True
    consistency_reasons: list[str] = []

    if records:
        # (a) Minimum row count check
        effective_min_rows = min_row_count
        if effective_min_rows is None and context and isinstance(context.get("row_count"), int):
            # If representative count or row count is provided, ensure non-zero
            effective_min_rows = 1

        if effective_min_rows is not None and len(records) < effective_min_rows:
            consistency_passed = False
            consistency_reasons.append(
                f"record count {len(records)} is below required minimum {effective_min_rows}"
            )

        # (b) Physical & bounds verification
        for idx, rec in enumerate(records):
            for k, val in rec.items():
                norm_k = _normalize_key(k)

                # Physical thickness/dimension check: values must be positive
                # Exclude boolean flag columns like below_min_acceptable_thickness
                if any(term in norm_k for term in ("thickness", "diameter", "length", "width")):
                    if not any(flag in norm_k for flag in ("below", "is_below", "flag", "status")):
                        if not isinstance(val, bool) and isinstance(val, (int, float)) and val <= 0:
                            consistency_passed = False
                            consistency_reasons.append(
                                f"record {idx} field '{k}' has non-positive measurement: {val}"
                            )

                # Custom bounds check
                if numeric_bounds and norm_k in numeric_bounds:
                    lower, upper = numeric_bounds[norm_k]
                    if not isinstance(val, bool) and isinstance(val, (int, float)) and not (lower <= val <= upper):
                        consistency_passed = False
                        consistency_reasons.append(
                            f"record {idx} field '{k}' value {val} out of bounds [{lower}, {upper}]"
                        )

            # (c) Logical threshold consistency:
            # If an item has measured/average thickness and minimum acceptable thickness
            # and a below_min flag:
            avg_val = None
            min_acc_val = None
            below_min_flag = None

            for k, v in rec.items():
                nk = _normalize_key(k)
                is_flag_field = any(t in nk for t in ("below", "is_below", "failed_min", "non_compliant"))
                is_minimum_field = (
                    not is_flag_field
                    and any(
                        t in nk
                        for t in (
                            "min_acceptable",
                            "minimum_acceptable",
                            "threshold",
                            "min_thickness",
                        )
                    )
                )
                if (
                    not is_minimum_field
                    and not is_flag_field
                    and any(t in nk for t in ("average", "measured", "avg", "thickness"))
                ):
                    if not isinstance(v, bool) and isinstance(v, (int, float)):
                        avg_val = float(v)
                if is_minimum_field:
                    if not isinstance(v, bool) and isinstance(v, (int, float)):
                        min_acc_val = float(v)
                if is_flag_field:
                    if isinstance(v, bool):
                        below_min_flag = v

            if avg_val is not None and min_acc_val is not None and below_min_flag is not None:
                expected_below = avg_val < min_acc_val
                if below_min_flag != expected_below:
                    consistency_passed = False
                    consistency_reasons.append(
                        f"record {idx} has inconsistent threshold flag: average={avg_val}, "
                        f"min_acceptable={min_acc_val}, below_min={below_min_flag} "
                        f"(expected {expected_below})"
                    )

    checks.append(
        VerificationCheck(
            name="computation_consistency",
            passed=consistency_passed,
            message=(
                "Computation outcome is logically and numerically consistent."
                if consistency_passed
                else f"Computation consistency check failed: {', '.join(consistency_reasons)}."
            ),
            details={"reasons": consistency_reasons},
        )
    )

    passed_count = sum(1 for c in checks if c.passed)
    failed_count = sum(1 for c in checks if not c.passed)
    overall_verified = (failed_count == 0)

    if overall_verified:
        summary = f"All {passed_count} deterministic verification checks passed."
    else:
        failed_names = [c.name for c in checks if not c.passed]
        summary = f"Verification failed on check(s): {', '.join(failed_names)}."

    return VerificationOutcome(
        verified=overall_verified,
        checks=checks,
        passed_count=passed_count,
        failed_count=failed_count,
        summary=summary,
        data=records if records else data,
    )


class VerifyResultCapability(Capability):
    """Deterministic capability applying verification rules to computation results.

    Implements the 'verify_result' capability in Workflow B.
    Runs completely without LLMs or external network access.
    """

    def __init__(self) -> None:
        self._metadata = CapabilityMetadata(
            name="verify_result",
            kind=CapabilityKind.TOOL,
            description="Apply deterministic verification rules to computation outputs.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "stdout": {"type": "string"},
                        "stderr": {"type": "string"},
                        "exit_code": {"type": "integer"},
                        "timed_out": {"type": "boolean"},
                        "data": {"type": ["object", "array", "string", "null"]},
                        "expected_fields": {"type": "array", "items": {"type": "string"}},
                        "computation_objective": {"type": "string"},
                        "min_row_count": {"type": "integer"},
                    },
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "verified": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "passed_count": {"type": "integer"},
                        "failed_count": {"type": "integer"},
                        "checks": {"type": "array"},
                    },
                    "required": ["verified", "summary", "passed_count", "failed_count"],
                }
            ),
            input_modalities=("spreadsheet", "document", "image"),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        """Apply deterministic verification rules to request inputs."""
        inputs = request.inputs or {}

        # Resolve stdout, stderr, exit_code, data from various alias structures
        stdout = inputs.get("stdout")
        stderr = inputs.get("stderr")
        exit_code = inputs.get("exit_code")
        timed_out = bool(inputs.get("timed_out", False))
        data = inputs.get("data")
        if data is None:
            data = inputs.get("result") or inputs.get("results") or inputs.get("output")

        # Nested execution_result or sandbox_result support
        nested = inputs.get("execution_result") or inputs.get("sandbox_result")
        if isinstance(nested, dict):
            stdout = stdout or nested.get("stdout")
            stderr = stderr or nested.get("stderr")
            if exit_code is None:
                exit_code = nested.get("exit_code")
            if not timed_out:
                timed_out = bool(nested.get("timed_out", False))
            if data is None:
                data = nested.get("data")

        expected_fields = inputs.get("expected_fields") or inputs.get("required_fields")
        if isinstance(expected_fields, str):
            expected_fields = [f.strip() for f in expected_fields.split(",") if f.strip()]

        computation_objective = (
            inputs.get("computation_objective")
            or inputs.get("user_goal")
            or inputs.get("objective")
            or inputs.get("computation_description")
        )

        min_row_count = inputs.get("min_row_count") or inputs.get("expected_row_count")
        numeric_bounds = inputs.get("numeric_bounds") or inputs.get("bounds")
        context = inputs.get("context") or inputs.get("computation_context")

        outcome = verify_computation_result(
            stdout=str(stdout) if stdout is not None else None,
            stderr=str(stderr) if stderr is not None else None,
            exit_code=int(exit_code) if exit_code is not None else None,
            timed_out=timed_out,
            data=data,
            expected_fields=list(expected_fields) if expected_fields else None,
            computation_objective=str(computation_objective) if computation_objective else None,
            min_row_count=int(min_row_count) if min_row_count is not None else None,
            numeric_bounds=numeric_bounds if isinstance(numeric_bounds, dict) else None,
            context=context if isinstance(context, dict) else None,
        )

        output: JsonObject = {
            "verified": outcome.verified,
            "summary": outcome.summary,
            "passed_count": outcome.passed_count,
            "failed_count": outcome.failed_count,
            "checks": [check.model_dump() for check in outcome.checks],
            "data": outcome.data,
        }

        observation = Observation(
            source="verify_result",
            kind="verification",
            summary=outcome.summary,
            data=output,
            request_id=request.request_id,
        )

        if outcome.verified:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.SUCCEEDED,
                output=output,
                observations=[observation],
            )

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.FAILED,
            error=outcome.summary,
            output=output,
            observations=[observation],
        )
