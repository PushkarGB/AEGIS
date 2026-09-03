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
from .generate_excel import GenerateExcelCapability, generate_excel_deliverable
from .verify_result import (
    VerificationCheck,
    VerificationOutcome,
    VerifyResultCapability,
    verify_computation_result,
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
    "GenerateExcelCapability",
    "generate_code",
    "generate_excel_deliverable",
    "InspectSpreadsheetCapability",
    "MockSandboxRunner",
    "RunCodeCapability",
    "SandboxResult",
    "SandboxRunner",
    "SheetInfo",
    "UnknownCapabilityError",
    "VerificationCheck",
    "VerificationOutcome",
    "VerifyResultCapability",
    "WorkbookInspection",
    "WorkbookMetadata",
    "inspect_spreadsheet",
    "run_code",
    "verify_computation_result",
]
