"""Provider-neutral model generation boundary.

The Agent, Controller, Broker, and future Router depend on this interface,
not on a local runtime, vendor SDK, or temporary API implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

MODEL_IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class ModelGenerationRequest(BaseModel):
    """The minimum provider-neutral input required to generate model text."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    model_id: str = Field(min_length=1, pattern=MODEL_IDENTIFIER_PATTERN)
    prompt: str = Field(min_length=1)
    system_prompt: str | None = Field(default=None, min_length=1)


class ModelGenerationResult(BaseModel):
    """Provider-neutral generated text associated with the selected model."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    model_id: str = Field(min_length=1, pattern=MODEL_IDENTIFIER_PATTERN)
    text: str = Field(min_length=1)


class ModelProvider(ABC):
    """Interchangeable synchronous model-text generation interface."""

    @abstractmethod
    def generate(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        """Generate text for a router-selected model without exposing provider details."""
