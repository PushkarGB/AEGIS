"""Computation workflow skill: prompt construction, input preparation, and observation parsing.

The skill sits between Agent semantic understanding and capability invocations
for `generate_code` and `run_code`. It never executes generated code directly.

Responsibilities:
- Accept user goal + inspected spreadsheet structure.
- Build structured code-generation prompts grounded in actual data schema.
- Prepare inputs for `generate_code` and `run_code` capability requests.
- Parse execution observations (stdout/stderr/exit status) for Agent reasoning.
- Build retry context from execution failures for corrective code generation.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.schemas import (
    CapabilityResult,
    CapabilityResultStatus,
    JsonObject,
)


class ComputationContext(BaseModel):
    """Everything the coding model needs to generate correct computation code."""

    model_config = ConfigDict(extra="forbid")

    user_goal: str = Field(min_length=1)
    file_path: str = Field(min_length=1)

    # Workbook structure extracted by inspect_spreadsheet
    sheet_names: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    numeric_fields: list[str] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    representative_values: dict[str, list[Any]] = Field(default_factory=dict)

    # Error context for retry/correction
    previous_code: str | None = Field(default=None, min_length=1)
    previous_error: str | None = Field(default=None, min_length=1)
    retry_attempt: int = Field(default=0, ge=0)


class CodeGenerationPrompt(BaseModel):
    """Structured prompt payload for the coding model."""

    model_config = ConfigDict(extra="forbid")

    computation_description: str = Field(min_length=1)
    data_schema: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    correction_context: str | None = Field(default=None, min_length=1)


class ExecutionOutcome(BaseModel):
    """Structured result from sandbox code execution for Agent reasoning."""

    model_config = ConfigDict(extra="forbid")

    succeeded: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error_summary: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SAFETY_CONSTRAINTS = [
    "Read data from the specified file path using openpyxl.",
    "Print results to stdout as a single JSON array of aggregated records (one per entity/group, e.g. equipment ID).",
    "Each record must be a JSON object containing the entity identifier, computed numeric metrics, and any threshold/compliance boolean flags.",
    "Do not print any text or commentary other than the JSON output.",
    "Do not write any files.",
    "Do not make network requests.",
    "Do not import packages beyond openpyxl and the Python standard library.",
    "Handle potential missing or empty cells gracefully.",
]


def _build_data_schema_summary(context: ComputationContext) -> str:
    """Create a concise textual description of the workbook schema."""

    lines: list[str] = []

    if context.sheet_names:
        lines.append(f"Sheets: {', '.join(context.sheet_names)}")

    if context.columns:
        lines.append(f"Columns: {', '.join(context.columns)}")

    if context.numeric_fields:
        lines.append(f"Numeric columns: {', '.join(context.numeric_fields)}")

    lines.append(f"Data rows: {context.row_count}")

    if context.representative_values:
        sample_parts: list[str] = []
        for col_name, values in context.representative_values.items():
            formatted = [str(v) for v in values[:5]]
            sample_parts.append(f"  {col_name}: {', '.join(formatted)}")
        if sample_parts:
            lines.append("Sample values:")
            lines.extend(sample_parts)

    return "\n".join(lines)


def build_code_generation_prompt(context: ComputationContext) -> CodeGenerationPrompt:
    """Transform a ComputationContext into a structured code-generation prompt.

    The prompt grounds the coding model in the actual data schema so it can
    generate correct, executable Python code without guessing column names
    or data types.
    """

    data_schema = _build_data_schema_summary(context)

    constraints = list(_SAFETY_CONSTRAINTS)
    constraints.insert(0, f"Read from file: {context.file_path}")

    correction_context: str | None = None
    if context.previous_error and context.previous_code:
        correction_context = (
            f"Previous code attempt (attempt {context.retry_attempt}) failed.\n"
            f"Error:\n{context.previous_error}\n\n"
            f"Previous code:\n{context.previous_code}\n\n"
            "Fix the error and generate corrected code."
        )

    return CodeGenerationPrompt(
        computation_description=context.user_goal,
        data_schema=data_schema,
        file_path=context.file_path,
        constraints=constraints,
        correction_context=correction_context,
    )


# ---------------------------------------------------------------------------
# Capability input preparation
# ---------------------------------------------------------------------------


def prepare_generate_code_inputs(prompt: CodeGenerationPrompt) -> JsonObject:
    """Package a CodeGenerationPrompt into capability request inputs."""

    inputs: JsonObject = {
        "computation_description": prompt.computation_description,
        "data_schema": prompt.data_schema,
        "file_path": prompt.file_path,
        "constraints": prompt.constraints,
    }
    if prompt.correction_context is not None:
        inputs["correction_context"] = prompt.correction_context
    return inputs


def prepare_run_code_inputs(code: str, file_path: str) -> JsonObject:
    """Package generated code and data file path for the run_code capability."""

    return {
        "code": code,
        "file_path": file_path,
    }


# ---------------------------------------------------------------------------
# Observation parsing
# ---------------------------------------------------------------------------


def parse_execution_observation(result: CapabilityResult) -> ExecutionOutcome:
    """Extract structured execution outcome from a run_code CapabilityResult."""

    stdout = str(result.output.get("stdout", ""))
    stderr = str(result.output.get("stderr", ""))
    exit_code_raw = result.output.get("exit_code")
    exit_code = int(exit_code_raw) if exit_code_raw is not None else None

    if result.status == CapabilityResultStatus.SUCCEEDED:
        return ExecutionOutcome(
            succeeded=True,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    error_summary = result.error or "Code execution failed."
    if stderr and stderr.strip() not in error_summary:
        error_summary = f"{error_summary.strip()}\nstderr:\n{stderr.strip()}"

    return ExecutionOutcome(
        succeeded=False,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        error_summary=error_summary,
    )


# ---------------------------------------------------------------------------
# Retry context
# ---------------------------------------------------------------------------


def build_retry_context(
    context: ComputationContext,
    outcome: ExecutionOutcome,
    previous_code: str,
) -> ComputationContext:
    """Return a new ComputationContext with error information for corrective generation.

    The original data schema and user goal are preserved. The coding model
    receives the failed code and error details so it can produce a correction.
    """

    return context.model_copy(
        update={
            "previous_code": previous_code,
            "previous_error": outcome.error_summary or outcome.stderr or "Unknown execution error.",
            "retry_attempt": context.retry_attempt + 1,
        }
    )
