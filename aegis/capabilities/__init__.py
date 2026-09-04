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
from .finish import FinishCapability
from .extract_document import ExtractDocumentCapability, extract_document_text
from .draft_approval_note import DraftApprovalNoteCapability
from .generate_word import GenerateWordCapability, create_approval_note_docx
from .generate_excel import GenerateExcelCapability, generate_excel_deliverable
from .verify_result import (
    VerificationCheck,
    VerificationOutcome,
    VerifyResultCapability,
    verify_computation_result,
    verify_document_drafting_result,
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
    "DraftApprovalNoteCapability",
    "DuplicateCapabilityError",
    "ExtractDocumentCapability",
    "FinishCapability",
    "GenerateCodeCapability",
    "GenerateExcelCapability",
    "GenerateWordCapability",
    "create_approval_note_docx",
    "extract_document_text",
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
    "verify_document_drafting_result",
]
