"""Provider-neutral capability invocation boundary and registry-backed Broker."""

from __future__ import annotations

from abc import ABC, abstractmethod

from aegis.capabilities import CapabilityRegistry
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
)


class CapabilityBroker(ABC):
    """Resolve and invoke registered capabilities on the Controller's behalf."""

    @abstractmethod
    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        """Invoke one bounded capability request without exposing its implementation."""


class RegistryCapabilityBroker(CapabilityBroker):
    """Resolve configured capability names and invoke registered implementations only."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        """Resolve, validate, and invoke a capability through the configured registry."""

        capability = self._registry.lookup(request.capability_name)
        if capability is None:
            return self._unavailable_result(request)

        if capability.metadata.name != request.capability_name:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.REJECTED,
                error="Resolved capability metadata does not match the requested name.",
            )

        try:
            return capability.invoke(request)
        except Exception as error:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=(
                    f"Capability '{request.capability_name}' raised "
                    f"{type(error).__name__}: {error}"
                ),
            )

    def _unavailable_result(self, request: CapabilityRequest) -> CapabilityResult:
        definition = self._registry.get_definition(request.capability_name)
        if definition is None:
            message = f"Unknown capability '{request.capability_name}'."
        elif not definition.enabled:
            message = f"Capability '{request.capability_name}' is disabled."
        else:
            message = f"Capability '{request.capability_name}' is not registered."

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.REJECTED,
            error=message,
        )
