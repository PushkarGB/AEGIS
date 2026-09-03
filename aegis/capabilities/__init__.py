"""Registered, bounded capabilities invoked only through the Capability Broker."""

from .base import Capability, CapabilityContract, CapabilityKind, CapabilityMetadata
from .generate_code import GenerateCodeCapability, generate_code
from .inspect_spreadsheet import (
    ColumnInfo,
    InspectSpreadsheetCapability,
    SheetInfo,
    WorkbookInspection,
    WorkbookMetadata,
    inspect_spreadsheet,
)
from .registry import (
    CapabilityRegistry,
    DisabledCapabilityError,
    DuplicateCapabilityError,
    UnknownCapabilityError,
)
from .run_code import (
    DockerSandboxRunner,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
    SandboxRunner,
    run_code,
)

__all__ = [
    "Capability",
    "CapabilityContract",
    "CapabilityKind",
    "CapabilityMetadata",
    "CapabilityRegistry",
    "ColumnInfo",
    "DisabledCapabilityError",
    "DockerSandboxRunner",
    "DuplicateCapabilityError",
    "GenerateCodeCapability",
    "generate_code",
    "InspectSpreadsheetCapability",
    "MockSandboxRunner",
    "RunCodeCapability",
    "SandboxResult",
    "SandboxRunner",
    "SheetInfo",
    "UnknownCapabilityError",
    "WorkbookInspection",
    "WorkbookMetadata",
    "inspect_spreadsheet",
    "run_code",
]
