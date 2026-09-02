"""Declarative state graphs for the bounded AEGIS prototype workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from aegis.schemas import ApprovalStatus


class WorkflowName(StrEnum):
    """The three prototype workflows governed by the Execution Controller."""

    COMPUTATION = "computation"
    SCANNED_DOCUMENT_APPROVAL = "scanned_document_approval"
    MULTIMODAL_ANALYSIS = "multimodal_analysis"


@dataclass(frozen=True)
class WorkflowDefinition:
    """A deterministic action graph with explicit success and failure transitions."""

    name: WorkflowName
    start_state: str
    actions_by_state: Mapping[str, frozenset[str]]
    success_states: Mapping[tuple[str, str], str | None]
    failure_states: Mapping[tuple[str, str], str]
    requires_approval: bool = False

    def allowed_actions(
        self, state_name: str | None, approval_status: ApprovalStatus
    ) -> frozenset[str]:
        """Return the only legal actions for a workflow state and approval status."""

        if state_name is None:
            return frozenset()

        actions = self.actions_by_state[state_name]
        if self.requires_approval and approval_status != ApprovalStatus.APPROVED:
            return actions - {"finish"}
        return actions

    def allows(
        self, state_name: str | None, action: str, approval_status: ApprovalStatus
    ) -> bool:
        """Check whether a proposed capability action is legal in this state."""

        return action in self.allowed_actions(state_name, approval_status)

    def next_state_on_success(self, state_name: str, action: str) -> str | None:
        """Return the state after a legal capability succeeds."""

        return self.success_states[(state_name, action)]

    def next_state_on_failure(self, state_name: str, action: str) -> str:
        """Return the corrective state after a legal capability fails."""

        return self.failure_states.get((state_name, action), state_name)


COMPUTATION_WORKFLOW = WorkflowDefinition(
    name=WorkflowName.COMPUTATION,
    start_state="inspect",
    actions_by_state={
        "inspect": frozenset({"inspect_spreadsheet"}),
        "generate": frozenset({"generate_code"}),
        "run": frozenset({"run_code"}),
        "verify": frozenset({"verify_result"}),
        "deliver": frozenset({"generate_excel"}),
        "finish": frozenset({"finish"}),
    },
    success_states={
        ("inspect", "inspect_spreadsheet"): "generate",
        ("generate", "generate_code"): "run",
        ("run", "run_code"): "verify",
        ("verify", "verify_result"): "deliver",
        ("deliver", "generate_excel"): "finish",
        ("finish", "finish"): None,
    },
    failure_states={
        # A failed sandbox execution returns to code generation for correction.
        ("run", "run_code"): "generate",
    },
)

SCANNED_DOCUMENT_APPROVAL_WORKFLOW = WorkflowDefinition(
    name=WorkflowName.SCANNED_DOCUMENT_APPROVAL,
    start_state="extract",
    actions_by_state={
        "extract": frozenset({"extract_document"}),
        "ocr": frozenset({"ocr_document"}),
        "knowledge_or_draft": frozenset({"search_knowledge", "draft_approval_note"}),
        "draft": frozenset({"draft_approval_note"}),
        "deliver": frozenset({"generate_word"}),
        "verify": frozenset({"verify_result"}),
        "finish": frozenset({"finish"}),
    },
    success_states={
        ("extract", "extract_document"): "ocr",
        ("ocr", "ocr_document"): "knowledge_or_draft",
        ("knowledge_or_draft", "search_knowledge"): "draft",
        ("knowledge_or_draft", "draft_approval_note"): "deliver",
        ("draft", "draft_approval_note"): "deliver",
        ("deliver", "generate_word"): "verify",
        ("verify", "verify_result"): "finish",
        ("finish", "finish"): None,
    },
    failure_states={},
    requires_approval=True,
)

MULTIMODAL_ANALYSIS_WORKFLOW = WorkflowDefinition(
    name=WorkflowName.MULTIMODAL_ANALYSIS,
    start_state="analyze",
    actions_by_state={
        "analyze": frozenset({"analyze_image"}),
        "verify": frozenset({"verify_result"}),
        "finish": frozenset({"finish"}),
    },
    success_states={
        ("analyze", "analyze_image"): "verify",
        ("verify", "verify_result"): "finish",
        ("finish", "finish"): None,
    },
    failure_states={},
)

WORKFLOWS: Mapping[WorkflowName, WorkflowDefinition] = {
    workflow.name: workflow
    for workflow in (
        COMPUTATION_WORKFLOW,
        SCANNED_DOCUMENT_APPROVAL_WORKFLOW,
        MULTIMODAL_ANALYSIS_WORKFLOW,
    )
}


def get_workflow(name: WorkflowName | str) -> WorkflowDefinition:
    """Return one registered prototype workflow definition."""

    return WORKFLOWS[WorkflowName(name)]
