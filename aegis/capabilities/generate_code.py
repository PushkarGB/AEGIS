"""GenerateCode capability: route code generation through ModelRouter → ModelProvider.

This capability accepts a structured computation prompt (data schema, constraints,
file path) and produces Python code via the configured coding model. It never
executes the generated code.
"""

from __future__ import annotations

import json

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.router import ModelGenerationRequest, ModelProvider, ModelRouter
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
)


def _build_system_prompt() -> str:
    """System prompt for the coding model that enforces output format."""

    return (
        "You are a Python code generator for industrial data computation. "
        "Return ONLY valid Python code. Do not include markdown fences, "
        "explanations, or commentary. The code must be directly executable."
    )


def _build_user_prompt(inputs: dict) -> str:
    """Assemble the user prompt from structured capability inputs."""

    parts: list[str] = []

    description = inputs.get("computation_description", "")
    if description:
        parts.append(f"Task: {description}")

    data_schema = inputs.get("data_schema", "")
    if data_schema:
        parts.append(f"\nData schema:\n{data_schema}")

    file_path = inputs.get("file_path", "")
    if file_path:
        parts.append(f"\nData file path: {file_path}")

    constraints = inputs.get("constraints", [])
    if constraints:
        constraint_lines = "\n".join(f"- {c}" for c in constraints)
        parts.append(f"\nConstraints:\n{constraint_lines}")

    correction_context = inputs.get("correction_context")
    if correction_context:
        parts.append(f"\nCorrection context:\n{correction_context}")

    parts.append(
        "\nGenerate Python code that performs the requested computation. "
        "Print all results to stdout."
    )

    return "\n".join(parts)


def _extract_code(raw_text: str) -> str:
    """Extract executable Python code from model output.

    Handles raw code, markdown-fenced code blocks, and leading/trailing whitespace.
    """

    stripped = raw_text.strip()

    # Try to extract from markdown code fences
    for fence_start in ("```python\n", "```python\r\n", "```\n", "```\r\n"):
        if stripped.startswith(fence_start):
            fence_end = stripped.rfind("```")
            if fence_end > len(fence_start):
                return stripped[len(fence_start):fence_end].strip()

    # Remove leading/trailing backtick fences without language tag
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = stripped[3:-3].strip()
        if inner:
            return inner

    return stripped


class GenerateCodeCapability(Capability):
    """Generate Python code through ModelRouter → ModelProvider without executing it."""

    def __init__(
        self,
        router: ModelRouter,
        providers: dict[str, ModelProvider],
    ) -> None:
        self._router = router
        self._providers = dict(providers)
        self._metadata = CapabilityMetadata(
            name="generate_code",
            kind=CapabilityKind.MODEL,
            description="Generate Python code for deterministic sandbox execution.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "computation_description": {
                            "type": "string",
                            "description": "What the code should compute.",
                        },
                        "data_schema": {
                            "type": "string",
                            "description": "Textual summary of the workbook schema.",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the data file the code will read.",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Safety and output constraints.",
                        },
                        "correction_context": {
                            "type": "string",
                            "description": "Previous error context for retry.",
                        },
                    },
                    "required": ["computation_description", "data_schema", "file_path"],
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

        if not inputs.get("computation_description"):
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Missing required input: computation_description.",
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
