"""Registered, bounded capabilities invoked only through the Capability Broker."""

from .base import Capability, CapabilityContract, CapabilityKind, CapabilityMetadata
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

__all__ = [
    "Capability",
    "CapabilityContract",
    "CapabilityKind",
    "CapabilityMetadata",
    "CapabilityRegistry",
    "ColumnInfo",
    "DisabledCapabilityError",
    "DuplicateCapabilityError",
    "InspectSpreadsheetCapability",
    "SheetInfo",
    "UnknownCapabilityError",
    "WorkbookInspection",
    "WorkbookMetadata",
    "inspect_spreadsheet",
]
