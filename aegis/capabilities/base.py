"""Implementation-neutral contracts for bounded AEGIS capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aegis.schemas import (
    CAPABILITY_PATTERN,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    JsonObject,
)


class CapabilityKind(StrEnum):
    """The bounded capability planes recognized by the prototype."""

    TOOL = "tool"
    MODEL = "model"
    KNOWLEDGE = "knowledge"
    CONTROL = "control"


class CapabilityContract(BaseModel):
    """A JSON-schema-compatible description of a capability boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    json_schema: JsonObject = Field(default_factory=dict)


class CapabilityMetadata(BaseModel):
    """Static capability metadata used for registration and safe dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, pattern=CAPABILITY_PATTERN)
    kind: CapabilityKind
    description: str = Field(min_length=1)
    input_contract: CapabilityContract = Field(default_factory=CapabilityContract)
    output_contract: CapabilityContract = Field(default_factory=CapabilityContract)
    input_modalities: tuple[str, ...] = ()


class Capability(ABC):
    """A bounded operation invoked by the Broker, never directly by the Agent."""

    @property
    @abstractmethod
    def metadata(self) -> CapabilityMetadata:
        """Return stable metadata and contracts for this capability."""

    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        """Reject mismatched requests before delegating to an implementation."""

        if request.capability_name != self.metadata.name:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.REJECTED,
                error=(
                    f"Request is for '{request.capability_name}', not capability "
                    f"'{self.metadata.name}'."
                ),
            )
        return self.execute(request)

    @abstractmethod
    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        """Execute one validated capability request."""
