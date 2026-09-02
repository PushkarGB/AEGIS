"""Tests for the provider-neutral capability contract and registry."""

from __future__ import annotations

import pytest

from aegis.capabilities import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
    CapabilityRegistry,
    DisabledCapabilityError,
    DuplicateCapabilityError,
    UnknownCapabilityError,
)
from aegis.config import CapabilityConfig, CapabilityRegistryConfig, load_config
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
)


class StaticCapability(Capability):
    """In-memory implementation used only to test common capability contracts."""

    def __init__(self, metadata: CapabilityMetadata) -> None:
        self._metadata = metadata
        self.executed_requests: list[CapabilityRequest] = []

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        self.executed_requests.append(request)
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"handled_by": self.metadata.name},
        )


def _metadata(name: str = "inspect_spreadsheet") -> CapabilityMetadata:
    return CapabilityMetadata(
        name=name,
        kind=CapabilityKind.TOOL,
        description="Inspect a spreadsheet using a deterministic test implementation.",
        input_contract=CapabilityContract(
            json_schema={"type": "object", "required": ["workbook"]}
        ),
        output_contract=CapabilityContract(
            json_schema={
                "type": "object",
                "properties": {"sheets": {"type": "array"}},
            }
        ),
        input_modalities=("spreadsheet",),
    )


def test_capability_exposes_metadata_contracts_and_safe_execution():
    capability = StaticCapability(_metadata())
    request = CapabilityRequest(
        capability_name="inspect_spreadsheet",
        inputs={"workbook": "uploads/readings.xlsx"},
    )

    result = capability.invoke(request)

    assert capability.metadata.input_contract.json_schema["required"] == ["workbook"]
    assert capability.metadata.output_contract.json_schema["type"] == "object"
    assert result.status == CapabilityResultStatus.SUCCEEDED
    assert result.output == {"handled_by": "inspect_spreadsheet"}
    assert capability.executed_requests == [request]


def test_capability_rejects_request_for_another_capability():
    capability = StaticCapability(_metadata())
    request = CapabilityRequest(capability_name="generate_excel")

    result = capability.invoke(request)

    assert result.status == CapabilityResultStatus.REJECTED
    assert "not capability 'inspect_spreadsheet'" in result.error
    assert capability.executed_requests == []


def test_registry_registers_and_lists_configured_capabilities_in_order():
    registry = CapabilityRegistry(load_config().capabilities)
    inspect = StaticCapability(_metadata("inspect_spreadsheet"))
    generate_excel = StaticCapability(
        CapabilityMetadata(
            name="generate_excel",
            kind=CapabilityKind.TOOL,
            description="Generate spreadsheet artifacts using a test implementation.",
        )
    )

    registry.register(generate_excel)
    registry.register(inspect)

    assert registry.lookup("inspect_spreadsheet") is inspect
    assert [capability.metadata.name for capability in registry.list_registered()] == [
        "inspect_spreadsheet",
        "generate_excel",
    ]
    assert registry.get_definition("generate_code").kind == "model"
    assert registry.list_configured(enabled_only=True)[0].name == "extract_document"


def test_registry_detects_duplicate_unknown_disabled_and_kind_mismatch_registration():
    registry = CapabilityRegistry(load_config().capabilities)
    inspect = StaticCapability(_metadata())
    registry.register(inspect)

    with pytest.raises(DuplicateCapabilityError):
        registry.register(inspect)

    with pytest.raises(UnknownCapabilityError):
        registry.register(StaticCapability(_metadata("unknown_capability")))

    disabled_registry = CapabilityRegistry(
        CapabilityRegistryConfig(
            capabilities=[
                CapabilityConfig(
                    name="inspect_spreadsheet",
                    kind="tool",
                    enabled=False,
                    description="Disabled test capability.",
                    handler_key="test.inspect",
                )
            ]
        )
    )
    with pytest.raises(DisabledCapabilityError):
        disabled_registry.register(StaticCapability(_metadata()))

    mismatch = StaticCapability(
        CapabilityMetadata(
            name="inspect_spreadsheet",
            kind=CapabilityKind.MODEL,
            description="Mismatched test capability.",
        )
    )
    mismatch_registry = CapabilityRegistry(load_config().capabilities)
    with pytest.raises(ValueError, match="kind does not match"):
        mismatch_registry.register(mismatch)


def test_registry_handles_unknown_or_disabled_lookup_without_raising():
    registry = CapabilityRegistry(load_config().capabilities)

    assert registry.lookup("unknown_capability") is None
    assert registry.get_definition("unknown_capability") is None
    assert registry.list_registered() == ()
