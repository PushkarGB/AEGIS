"""GenerateCode capability: route code generation through ModelRouter → ModelProvider.

This capability accepts a structured computation prompt (computation objective,
relevant spreadsheet structure/data description, required output constraints)
and produces executable Python code via the configured coding model.
It never executes the generated code.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.router import (
    ModelGenerationRequest,
    ModelProvider,
    ModelRouter,
    RoutingDecision,
)
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
)


_DEFAULT_OUTPUT_CONSTRAINTS: list[str] = [
    "Read data using openpyxl from the specified file path.",
    "Print all results to stdout as structured, human-readable text.",
    "Do not write any files.",
    "Do not make external network requests.",
    "Do not import packages beyond openpyxl and the Python standard library.",
    "Handle potential missing or empty cells gracefully.",
    "Return only valid, directly executable Python code.",
]


def _build_system_prompt() -> str:
    """System prompt for the coding model that enforces output format."""
    return (
        "You are an expert Python code generator for industrial data computation. "
        "Return ONLY valid, directly executable Python code. Do not include markdown fences, "
        "explanations, or commentary. The code must be directly executable."
    )


def _resolve_objective(inputs: dict[str, Any]) -> str | None:
    """Resolve computation objective from various input key aliases."""
    for key in (
        "computation_objective",
        "computation_description",
        "computation",
        "objective",
        "user_goal",
        "goal",
        "task",
    ):
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _resolve_structure(inputs: dict[str, Any]) -> str | None:
    """Resolve spreadsheet structure or data description from input key aliases."""
    for key in (
        "relevant_spreadsheet_structure",
        "spreadsheet_structure",
        "data_description",
        "data_schema",
        "structure",
        "schema",
    ):
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            try:
                return json.dumps(val, indent=2)
            except (TypeError, ValueError):
                return str(val)
    return None


def _resolve_constraints(inputs: dict[str, Any]) -> list[str]:
    """Resolve output constraints from input key aliases, falling back to defaults."""
    for key in (
        "required_output_constraints",
        "output_constraints",
        "constraints",
    ):
        val = inputs.get(key)
        if isinstance(val, list):
            items = [str(item).strip() for item in val if str(item).strip()]
            if items:
                return items
        elif isinstance(val, str) and val.strip():
            return [val.strip()]
    return list(_DEFAULT_OUTPUT_CONSTRAINTS)


def _resolve_file_path(inputs: dict[str, Any]) -> str | None:
    """Resolve file path from input key aliases."""
    for key in ("file_path", "workbook", "path", "filepath", "file"):
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _resolve_correction_context(inputs: dict[str, Any]) -> str | None:
    """Resolve previous error or correction context for retries."""
    for key in ("correction_context", "previous_error", "error_context"):
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _build_user_prompt(inputs: dict[str, Any]) -> str:
    """Assemble the user prompt from structured capability inputs.

    The Coding Model receives:
    - computation objective;
    - relevant spreadsheet structure/data description;
    - required output constraints.
    """
    parts: list[str] = []

    objective = _resolve_objective(inputs)
    if objective:
        parts.append(f"Computation Objective:\n{objective}")

    structure = _resolve_structure(inputs)
    if structure:
        parts.append(f"\nRelevant Spreadsheet Structure / Data Description:\n{structure}")

    file_path = _resolve_file_path(inputs)
    if file_path:
        parts.append(f"\nSpreadsheet File Path: {file_path}")

    constraints = _resolve_constraints(inputs)
    if constraints:
        constraint_lines = "\n".join(f"- {c}" for c in constraints)
        parts.append(f"\nRequired Output Constraints:\n{constraint_lines}")

    correction_context = _resolve_correction_context(inputs)
    if correction_context:
        parts.append(f"\nCorrection Context (previous failure):\n{correction_context}")

    parts.append(
        "\nGenerate valid, executable Python code that fulfills the computation objective "
        "using the spreadsheet structure and adhering strictly to all output constraints. "
        "Print all results to stdout."
    )

    return "\n".join(parts)


def _extract_code(raw_text: str) -> str:
    """Extract executable Python code from model output.

    Handles raw code, markdown-fenced code blocks, and leading/trailing whitespace
    or surrounding commentary.
    """
    stripped = raw_text.strip()

    # Match ```python ... ``` or ``` ... ```
    match = re.search(r"```(?:python)?\s*\n?(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if match:
        extracted = match.group(1).strip()
        if extracted:
            return extracted

    # Remove leading/trailing backtick fences without language tag if matched at boundary
    if stripped.startswith("```") and stripped.endswith("```") and len(stripped) >= 6:
        inner = stripped[3:-3].strip()
        if inner.startswith("python"):
            inner = inner[6:].strip()
        if inner:
            return inner

    return stripped


def generate_code(
    router: ModelRouter,
    provider: ModelProvider | dict[str, ModelProvider],
    *,
    computation_objective: str,
    spreadsheet_structure: str | dict[str, Any] | None = None,
    output_constraints: list[str] | str | None = None,
    file_path: str | None = None,
    correction_context: str | None = None,
) -> str:
    """Generate executable Python code for computation via ModelRouter and ModelProvider.

    The Coding Model receives:
    - computation objective;
    - relevant spreadsheet structure/data description;
    - required output constraints.

    Returns executable Python code. Does NOT execute the generated code.
    """
    if not computation_objective or not computation_objective.strip():
        raise ValueError("computation_objective must be a non-empty string.")

    # 1. Route to the coding model
    routing: RoutingDecision = router.route("code_generation", modality="spreadsheet")

    # 2. Resolve provider
    resolved_provider: ModelProvider | None = None
    if isinstance(provider, ModelProvider):
        resolved_provider = provider
    elif isinstance(provider, dict):
        resolved_provider = provider.get(routing.provider_id)
        if resolved_provider is None and len(provider) == 1:
            resolved_provider = next(iter(provider.values()))

    if resolved_provider is None:
        raise ValueError(f"No ModelProvider found for provider ID '{routing.provider_id}'.")

    # 3. Assemble inputs and build generation request
    inputs: dict[str, Any] = {
        "computation_objective": computation_objective.strip(),
    }
    if spreadsheet_structure is not None:
        inputs["spreadsheet_structure"] = spreadsheet_structure
    if output_constraints is not None:
        inputs["output_constraints"] = output_constraints
    if file_path is not None:
        inputs["file_path"] = file_path.strip()
    if correction_context is not None:
        inputs["correction_context"] = correction_context.strip()

    prompt = _build_user_prompt(inputs)
    system_prompt = _build_system_prompt()

    gen_request = ModelGenerationRequest(
        model_id=routing.model_id,
        prompt=prompt,
        system_prompt=system_prompt,
    )

    # 4. Generate via provider
    gen_result = resolved_provider.generate(gen_request)

    # 5. Extract and validate code
    code = _extract_code(gen_result.text)
    if not code:
        raise ValueError("Coding model returned empty code.")

    return code


class GenerateCodeCapability(Capability):
    """Generate Python code through ModelRouter → ModelProvider without executing it."""

    def __init__(
        self,
        router: ModelRouter,
        providers: dict[str, ModelProvider] | ModelProvider | None = None,
        *,
        provider: ModelProvider | None = None,
    ) -> None:
        self._router = router
        if isinstance(providers, ModelProvider):
            self._providers = {providers.__class__.__name__: providers}
            self._default_provider: ModelProvider | None = providers
        elif isinstance(providers, dict):
            self._providers = dict(providers)
            self._default_provider = (
                next(iter(providers.values())) if len(providers) == 1 else None
            )
        elif providers is None and provider is not None:
            self._providers = {provider.__class__.__name__: provider}
            self._default_provider = provider
        else:
            self._providers = {}
            self._default_provider = None

        self._metadata = CapabilityMetadata(
            name="generate_code",
            kind=CapabilityKind.MODEL,
            description="Generate code for deterministic sandbox execution.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "computation_objective": {
                            "type": "string",
                            "description": "Computation goal or objective.",
                        },
                        "computation_description": {
                            "type": "string",
                            "description": "What the code should compute.",
                        },
                        "spreadsheet_structure": {
                            "type": ["string", "object"],
                            "description": "Structure or schema of the spreadsheet.",
                        },
                        "data_description": {
                            "type": ["string", "object"],
                            "description": "Description of the data and columns.",
                        },
                        "data_schema": {
                            "type": ["string", "object"],
                            "description": "Textual or structured summary of the workbook schema.",
                        },
                        "required_output_constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Required output and safety constraints.",
                        },
                        "output_constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Constraints on execution output.",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Safety and output constraints.",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the data file the code will read.",
                        },
                        "correction_context": {
                            "type": "string",
                            "description": "Previous error context for retry.",
                        },
                    },
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "model_id": {"type": "string"},
                    },
                    "required": ["code"],
                }
            ),
            input_modalities=("spreadsheet",),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        """Route code generation to the coding model and return generated code."""
        inputs = request.inputs

        objective = _resolve_objective(inputs)
        if not objective:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Missing required input: computation_description / computation_objective.",
            )

        # Route to the coding model
        try:
            routing = self._router.route(
                "code_generation",
                modality="spreadsheet",
            )
        except Exception as exc:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Model routing failed: {exc}",
            )

        provider = self._providers.get(routing.provider_id)
        if provider is None and self._default_provider is not None:
            provider = self._default_provider

        if provider is None:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"No provider instance for '{routing.provider_id}'.",
            )

        # Build model request
        gen_request = ModelGenerationRequest(
            model_id=routing.model_id,
            system_prompt=_build_system_prompt(),
            prompt=_build_user_prompt(inputs),
        )

        try:
            gen_result = provider.generate(gen_request)
        except Exception as exc:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Code generation model failed: {exc}",
            )

        code = _extract_code(gen_result.text)
        if not code:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Model returned empty code.",
            )

        observation = Observation(
            source="generate_code",
            kind="code_generated",
            summary=f"Generated Python code ({len(code)} chars) via model '{routing.model_id}'.",
            data={
                "model_id": routing.model_id,
                "provider_id": routing.provider_id,
                "code_length": len(code),
            },
            request_id=request.request_id,
        )

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"code": code, "model_id": routing.model_id},
            observations=[observation],
        )
