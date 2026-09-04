"""Finish capability: mark workflow as completed under Controller governance."""

from __future__ import annotations

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
    Observation,
)


class FinishCapability(Capability):
    """Mark the workflow as ready to finish."""

    def __init__(self) -> None:
        self._metadata = CapabilityMetadata(
            name="finish",
            kind=CapabilityKind.CONTROL,
            description="Mark the workflow as ready to finish.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {},
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                    },
                }
            ),
            input_modalities=("spreadsheet", "document", "image"),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        observation = Observation(
            source="finish",
            kind="workflow_completed",
            summary="Workflow marked ready to finish.",
            data={"status": "completed"},
        )
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"status": "completed"},
            observations=[observation],
        )
