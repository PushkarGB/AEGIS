"""Deterministic registry for externally configured capability definitions."""

from __future__ import annotations

from collections.abc import Iterable

from aegis.config import CapabilityConfig, CapabilityRegistryConfig

from .base import Capability, CapabilityKind


class DuplicateCapabilityError(ValueError):
    """Raised when attempting to register the same capability name twice."""


class DisabledCapabilityError(ValueError):
    """Raised when attempting to register a capability disabled by configuration."""


class UnknownCapabilityError(LookupError):
    """Raised when a capability is not present in the configured registry."""


class CapabilityRegistry:
    """Register only configured, enabled capability implementations by name."""

    def __init__(self, config: CapabilityRegistryConfig) -> None:
        self._definitions = {
            definition.name: definition for definition in config.capabilities
        }
        self._registration_order = tuple(definition.name for definition in config.capabilities)
        self._registered: dict[str, Capability] = {}

    @classmethod
    def from_definitions(
        cls, definitions: Iterable[CapabilityConfig]
    ) -> "CapabilityRegistry":
        """Create a registry from validated configured definitions."""

        return cls(CapabilityRegistryConfig(capabilities=list(definitions)))

    def register(self, capability: Capability) -> None:
        """Register one implementation after checking external configuration authority."""

        metadata = capability.metadata
        definition = self._definitions.get(metadata.name)
        if definition is None:
            raise UnknownCapabilityError(
                f"Capability '{metadata.name}' is not configured."
            )
        if not definition.enabled:
            raise DisabledCapabilityError(
                f"Capability '{metadata.name}' is disabled by configuration."
            )
        if metadata.kind.value != definition.kind:
            raise ValueError(
                f"Capability '{metadata.name}' kind does not match its configuration."
            )
        if metadata.name in self._registered:
            raise DuplicateCapabilityError(
                f"Capability '{metadata.name}' is already registered."
            )
        self._registered[metadata.name] = capability

    def lookup(self, name: str) -> Capability | None:
        """Return an enabled registered capability, or None when it is unavailable."""

        definition = self._definitions.get(name)
        if definition is None or not definition.enabled:
            return None
        return self._registered.get(name)

    def get_definition(self, name: str) -> CapabilityConfig | None:
        """Return configured metadata for a known capability without implementation lookup."""

        return self._definitions.get(name)

    def list_configured(self, *, enabled_only: bool = False) -> tuple[CapabilityConfig, ...]:
        """List configured definitions in their declared deterministic order."""

        definitions = tuple(
            self._definitions[name] for name in self._registration_order
        )
        if enabled_only:
            return tuple(definition for definition in definitions if definition.enabled)
        return definitions

    def list_registered(self) -> tuple[Capability, ...]:
        """List registered implementations in configured declaration order."""

        return tuple(
            self._registered[name]
            for name in self._registration_order
            if name in self._registered
        )
