"""Tests for declarative legal workflow transitions."""

from aegis.orchestration.workflows import WorkflowName, get_workflow
from aegis.schemas import ApprovalStatus


def test_computation_workflow_allows_only_ordered_actions():
    workflow = get_workflow(WorkflowName.COMPUTATION)

    assert workflow.allowed_actions("inspect", ApprovalStatus.NOT_REQUIRED) == {
        "inspect_spreadsheet"
    }
    assert not workflow.allows("inspect", "run_code", ApprovalStatus.NOT_REQUIRED)
    assert workflow.next_state_on_success("inspect", "inspect_spreadsheet") == "generate"
    assert workflow.next_state_on_success("generate", "generate_code") == "run"
    assert workflow.next_state_on_failure("run", "run_code") == "generate"


def test_scanned_document_workflow_allows_optional_knowledge_before_draft():
    workflow = get_workflow(WorkflowName.SCANNED_DOCUMENT_APPROVAL)

    assert workflow.next_state_on_success("ocr", "ocr_document") == "knowledge_or_draft"
    assert workflow.allowed_actions("knowledge_or_draft", ApprovalStatus.PENDING) == {
        "search_knowledge",
        "draft_approval_note",
    }
    assert workflow.next_state_on_success("knowledge_or_draft", "search_knowledge") == "draft"
    assert not workflow.allows("draft", "search_knowledge", ApprovalStatus.PENDING)


def test_scanned_document_finish_requires_human_approval():
    workflow = get_workflow(WorkflowName.SCANNED_DOCUMENT_APPROVAL)

    assert not workflow.allows("finish", "finish", ApprovalStatus.PENDING)
    assert workflow.allows("finish", "finish", ApprovalStatus.APPROVED)


def test_multimodal_workflow_rejects_out_of_order_actions():
    workflow = get_workflow(WorkflowName.MULTIMODAL_ANALYSIS)

    assert workflow.allows("analyze", "analyze_image", ApprovalStatus.NOT_REQUIRED)
    assert not workflow.allows("analyze", "verify_result", ApprovalStatus.NOT_REQUIRED)
    assert workflow.next_state_on_success("analyze", "analyze_image") == "verify"
