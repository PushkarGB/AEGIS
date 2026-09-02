"""Provider-neutral capability invocation boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod

from aegis.schemas import CapabilityRequest, CapabilityResult


class CapabilityBroker(ABC):
    """Resolve and invoke registered capabilities on the Controller's behalf."""

    @abstractmethod
    def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        """Invoke one bounded capability request without exposing its implementation."""
