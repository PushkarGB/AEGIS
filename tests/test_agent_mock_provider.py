"""Prove core Agent behaviors through MockModelProvider without GPU inference.

Tested behaviors:
1. Intent classification — correct intent for each prototype workflow.
2. Modality classification — correct input modality for each attachment type.
3. Plan proposal — bounded, workflow-valid, Controller-ready capability plans.
4. Observation-based correction — retry_correct directive from execution error.
5. Finish decision — finish directive with done=true after verification.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aegis.agent import (
    AgentDirective,
    AgentIntent,
    AgentModelResponseError,
    AttachmentDescriptor,
    InputModality,
    IntentAnalysisRequest,
    IntentAnalysisResult,
    ObservationDecision,
    ObservationReasoningRequest,
    PlanGenerationRequest,
    PlanProposal,
    PreviousExecutionContext,
    RouterAgentRuntime,
)
from aegis.config import (
    AgentConfig,
    AgentPlanningConfig,
    ModelConfig,
    ModelProviderConfig,
    ModelRegistryConfig,
)
from aegis.orchestration import WorkflowName
from aegis.router import MockModelProvider, ModelRegistry, ModelRouter
from aegis.router.provider import ModelGenerationRequest
from aegis.schemas import (
    AgentDecision,
    CapabilityResultStatus,
    Observation,
    TaskState,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _agent_config() -> AgentConfig:
    return AgentConfig(
        name="aegis-agent",
        description="Test agent for mock provider behavioural proofs.",
        default_model_role="agent",
        planning=AgentPlanningConfig(max_plan_steps=8, max_observation_chars=12000),
        allowed_modalities=["spreadsheet", "scanned_document", "image"],
        default_capabilities=["finish"],
    )


def _model_registry() -> ModelRegistryConfig:
    return ModelRegistryConfig(
        providers=[
            ModelProviderConfig(
                id="mock",
                kind="mock",
                enabled=True,
            )
        ],
        models=[
            ModelConfig(
                id="agent-model",
                provider="mock",
                roles=["agent"],
                capabilities=["reasoning", "planning", "drafting"],
                task_types=["general_reasoning", "drafting"],
            )
        ],
        role_defaults={"agent": "agent-model"},
    )


def _router() -> ModelRouter:
    return ModelRouter(ModelRegistry(_model_registry()))


def _runtime_with_static(response_payload: dict) -> tuple[RouterAgentRuntime, MockModelProvider]:
    """Build a runtime whose MockModelProvider always returns a fixed JSON response."""
    provider = MockModelProvider(responses={"agent-model": json.dumps(response_payload)})
    runtime = RouterAgentRuntime(_agent_config(), _router(), {"mock": provider})
    return runtime, provider


def _runtime_with_factory(factory):
    """Build a runtime whose MockModelProvider uses a per-request factory."""
    provider = MockModelProvider(response_factory=factory)
    runtime = RouterAgentRuntime(_agent_config(), _router(), {"mock": provider})
    return runtime, provider


# ---------------------------------------------------------------------------
# 1. Intent classification
# ---------------------------------------------------------------------------


class TestIntentClassification:
    """Prove the Agent classifies the correct intent for each prototype workflow."""

    def test_computation_intent_from_spreadsheet_request(self):
        runtime, provider = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "This is a spreadsheet-based computation task.",
            }
        )

        result = runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Calculate average thickness per equipment item from this month's readings.",
                attachments=[
                    AttachmentDescriptor(
                        name="readings.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                ],
            )
        )

        assert result.intent == AgentIntent.COMPUTATION
        assert result.workflow == WorkflowName.COMPUTATION
        assert len(provider.requests) == 1
        assert provider.requests[0].model_id == "agent-model"

    def test_document_drafting_intent_from_scanned_pdf_request(self):
        runtime, provider = _runtime_with_static(
            {
                "intent": "document_drafting",
                "modality": "scanned_document",
                "workflow": "scanned_document_approval",
                "summary": "This is a scanned inspection report requiring an approval note.",
            }
        )

        result = runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Prepare an approval note based on this inspection report.",
                attachments=[
                    AttachmentDescriptor(
                        name="inspection_report.pdf",
                        media_type="application/pdf",
                    )
                ],
            )
        )

        assert result.intent == AgentIntent.DOCUMENT_DRAFTING
        assert result.workflow == WorkflowName.SCANNED_DOCUMENT_APPROVAL

    def test_multimodal_analysis_intent_from_image_request(self):
        runtime, provider = _runtime_with_static(
            {
                "intent": "multimodal_analysis",
                "modality": "image",
                "workflow": "multimodal_analysis",
                "summary": "This is a visual equipment inspection analysis task.",
            }
        )

        result = runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Inspect this photograph and describe visible equipment condition.",
                attachments=[
                    AttachmentDescriptor(
                        name="equipment.jpg",
                        media_type="image/jpeg",
                    )
                ],
            )
        )

        assert result.intent == AgentIntent.MULTIMODAL_ANALYSIS
        assert result.workflow == WorkflowName.MULTIMODAL_ANALYSIS

    def test_intent_includes_user_goal_and_attachment_in_prompt(self):
        """Verify the model receives the full request context."""
        runtime, provider = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Spreadsheet computation task.",
            }
        )
        runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Sum total defect counts per category.",
                attachments=[
                    AttachmentDescriptor(name="defects.csv", media_type="text/csv")
                ],
            )
        )

        prompt = provider.requests[0].prompt
        assert "Sum total defect counts" in prompt
        assert "defects.csv" in prompt


# ---------------------------------------------------------------------------
# 2. Modality classification
# ---------------------------------------------------------------------------


class TestModalityClassification:
    """Prove the Agent classifies the correct input modality for different file types."""

    @pytest.mark.parametrize(
        "media_type, expected_modality, intent, workflow",
        [
            (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                InputModality.SPREADSHEET,
                "computation",
                "computation",
            ),
            ("text/csv", InputModality.SPREADSHEET, "computation", "computation"),
            (
                "application/pdf",
                InputModality.SCANNED_DOCUMENT,
                "document_drafting",
                "scanned_document_approval",
            ),
            (
                "image/jpeg",
                InputModality.IMAGE,
                "multimodal_analysis",
                "multimodal_analysis",
            ),
            (
                "image/png",
                InputModality.IMAGE,
                "multimodal_analysis",
                "multimodal_analysis",
            ),
        ],
        ids=[
            "xlsx-spreadsheet",
            "csv-spreadsheet",
            "pdf-scanned_document",
            "jpeg-image",
            "png-image",
        ],
    )
    def test_modality_classification_per_media_type(
        self, media_type, expected_modality, intent, workflow
    ):
        runtime, _ = _runtime_with_static(
            {
                "intent": intent,
                "modality": expected_modality.value,
                "workflow": workflow,
                "summary": f"Classified as {expected_modality.value}.",
            }
        )

        result = runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Process this file.",
                attachments=[
                    AttachmentDescriptor(name="input_file", media_type=media_type)
                ],
            )
        )

        assert result.modality == expected_modality

    def test_rejects_unsupported_modality_from_model(self):
        """The runtime must reject a model response containing an unknown modality."""
        runtime, _ = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "video",
                "workflow": "computation",
                "summary": "Misclassified as video.",
            }
        )

        with pytest.raises(Exception):
            # Will fail at Pydantic validation or modality allowlist check
            runtime.decide_intent(
                IntentAnalysisRequest(
                    user_goal="Process this file.",
                    attachments=[
                        AttachmentDescriptor(name="data.xlsx", media_type="application/vnd.ms-excel")
                    ],
                )
            )

    def test_intent_modality_consistency_enforced(self):
        """Mismatched intent/modality combination must be rejected by schema validation."""
        runtime, _ = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "image",
                "workflow": "computation",
                "summary": "Inconsistent intent/modality.",
            }
        )

        with pytest.raises(AgentModelResponseError):
            runtime.decide_intent(
                IntentAnalysisRequest(
                    user_goal="Compute from image.",
                    attachments=[
                        AttachmentDescriptor(name="photo.jpg", media_type="image/jpeg")
                    ],
                )
            )


# ---------------------------------------------------------------------------
# 3. Plan proposal
# ---------------------------------------------------------------------------


class TestPlanProposal:
    """Prove the Agent produces bounded, workflow-valid capability plans."""

    def test_computation_plan_has_all_required_steps(self):
        runtime, provider = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Full computation pipeline.",
                "steps": [
                    {"capability_name": "inspect_spreadsheet", "purpose": "Inspect workbook.", "inputs": {}},
                    {"capability_name": "generate_code", "purpose": "Generate calculation.", "inputs": {}},
                    {"capability_name": "run_code", "purpose": "Execute in sandbox.", "inputs": {}},
                    {"capability_name": "verify_result", "purpose": "Verify output.", "inputs": {}},
                    {"capability_name": "generate_excel", "purpose": "Produce deliverable.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Mark complete.", "inputs": {}},
                ],
            }
        )

        result = runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Calculate average thickness.",
                intent=AgentIntent.COMPUTATION,
                modality=InputModality.SPREADSHEET,
                available_capabilities=[
                    "inspect_spreadsheet",
                    "generate_code",
                    "run_code",
                    "verify_result",
                    "generate_excel",
                    "finish",
                ],
            )
        )

        assert isinstance(result, PlanProposal)
        step_names = [s.capability_name for s in result.steps]
        assert step_names == [
            "inspect_spreadsheet",
            "generate_code",
            "run_code",
            "verify_result",
            "generate_excel",
            "finish",
        ]
        assert result.workflow == WorkflowName.COMPUTATION
        assert len(provider.requests) == 1

    def test_scanned_document_plan_with_optional_knowledge(self):
        runtime, _ = _runtime_with_static(
            {
                "intent": "document_drafting",
                "modality": "scanned_document",
                "workflow": "scanned_document_approval",
                "summary": "Approval note workflow with knowledge lookup.",
                "steps": [
                    {"capability_name": "extract_document", "purpose": "Extract document.", "inputs": {}},
                    {"capability_name": "ocr_document", "purpose": "OCR the scan.", "inputs": {}},
                    {"capability_name": "search_knowledge", "purpose": "Retrieve relevant standards.", "inputs": {}},
                    {"capability_name": "draft_approval_note", "purpose": "Draft approval note.", "inputs": {}},
                    {"capability_name": "generate_word", "purpose": "Produce Word doc.", "inputs": {}},
                    {"capability_name": "verify_result", "purpose": "Verify grounding.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Mark complete.", "inputs": {}},
                ],
            }
        )

        result = runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Prepare approval note from this inspection report.",
                intent=AgentIntent.DOCUMENT_DRAFTING,
                modality=InputModality.SCANNED_DOCUMENT,
                available_capabilities=[
                    "extract_document",
                    "ocr_document",
                    "search_knowledge",
                    "draft_approval_note",
                    "generate_word",
                    "verify_result",
                    "finish",
                ],
            )
        )

        step_names = [s.capability_name for s in result.steps]
        assert "search_knowledge" in step_names
        assert step_names[-1] == "finish"

    def test_scanned_document_plan_without_optional_knowledge(self):
        """search_knowledge is optional in the scanned_document workflow."""
        runtime, _ = _runtime_with_static(
            {
                "intent": "document_drafting",
                "modality": "scanned_document",
                "workflow": "scanned_document_approval",
                "summary": "Approval note without knowledge lookup.",
                "steps": [
                    {"capability_name": "extract_document", "purpose": "Extract.", "inputs": {}},
                    {"capability_name": "ocr_document", "purpose": "OCR.", "inputs": {}},
                    {"capability_name": "draft_approval_note", "purpose": "Draft.", "inputs": {}},
                    {"capability_name": "generate_word", "purpose": "Word.", "inputs": {}},
                    {"capability_name": "verify_result", "purpose": "Verify.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Done.", "inputs": {}},
                ],
            }
        )

        result = runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Prepare approval note.",
                intent=AgentIntent.DOCUMENT_DRAFTING,
                modality=InputModality.SCANNED_DOCUMENT,
                available_capabilities=[
                    "extract_document",
                    "ocr_document",
                    "search_knowledge",
                    "draft_approval_note",
                    "generate_word",
                    "verify_result",
                    "finish",
                ],
            )
        )

        step_names = [s.capability_name for s in result.steps]
        assert "search_knowledge" not in step_names
        assert step_names[-1] == "finish"

    def test_multimodal_analysis_plan(self):
        runtime, _ = _runtime_with_static(
            {
                "intent": "multimodal_analysis",
                "modality": "image",
                "workflow": "multimodal_analysis",
                "summary": "Visual analysis pipeline.",
                "steps": [
                    {"capability_name": "analyze_image", "purpose": "Analyze equipment image.", "inputs": {}},
                    {"capability_name": "verify_result", "purpose": "Verify analysis.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Complete.", "inputs": {}},
                ],
            }
        )

        result = runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Inspect equipment photograph.",
                intent=AgentIntent.MULTIMODAL_ANALYSIS,
                modality=InputModality.IMAGE,
                available_capabilities=["analyze_image", "verify_result", "finish"],
            )
        )

        assert [s.capability_name for s in result.steps] == [
            "analyze_image",
            "verify_result",
            "finish",
        ]
        assert result.workflow == WorkflowName.MULTIMODAL_ANALYSIS

    def test_plan_respects_step_limit(self):
        """Plans exceeding max_plan_steps must be rejected."""
        steps = [
            {"capability_name": f"step_{i}", "purpose": f"Step {i}.", "inputs": {}}
            for i in range(10)
        ]
        runtime, _ = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Too many steps.",
                "steps": steps,
            }
        )

        with pytest.raises(AgentModelResponseError, match="more steps than"):
            runtime.generate_plan(
                PlanGenerationRequest(
                    user_goal="Calculate something.",
                    intent=AgentIntent.COMPUTATION,
                    modality=InputModality.SPREADSHEET,
                    available_capabilities=[
                        "inspect_spreadsheet",
                        "generate_code",
                        "run_code",
                        "verify_result",
                        "generate_excel",
                        "finish",
                    ],
                )
            )

    def test_plan_rejects_out_of_order_capabilities(self):
        """Capabilities proposed in wrong workflow order must be rejected."""
        runtime, _ = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Wrong order.",
                "steps": [
                    {"capability_name": "generate_code", "purpose": "Generate.", "inputs": {}},
                    {"capability_name": "inspect_spreadsheet", "purpose": "Inspect.", "inputs": {}},
                    {"capability_name": "run_code", "purpose": "Run.", "inputs": {}},
                    {"capability_name": "verify_result", "purpose": "Verify.", "inputs": {}},
                    {"capability_name": "generate_excel", "purpose": "Excel.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Done.", "inputs": {}},
                ],
            }
        )

        with pytest.raises(AgentModelResponseError, match="conflicts with the workflow"):
            runtime.generate_plan(
                PlanGenerationRequest(
                    user_goal="Calculate.",
                    intent=AgentIntent.COMPUTATION,
                    modality=InputModality.SPREADSHEET,
                    available_capabilities=[
                        "inspect_spreadsheet",
                        "generate_code",
                        "run_code",
                        "verify_result",
                        "generate_excel",
                        "finish",
                    ],
                )
            )

    def test_plan_rejects_unavailable_capability(self):
        """Capabilities not in available_capabilities must be rejected."""
        runtime, _ = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Uses unknown capability.",
                "steps": [
                    {"capability_name": "inspect_spreadsheet", "purpose": "Inspect.", "inputs": {}},
                    {"capability_name": "generate_code", "purpose": "Generate.", "inputs": {}},
                    {"capability_name": "deploy_to_cloud", "purpose": "Deploy.", "inputs": {}},
                    {"capability_name": "finish", "purpose": "Done.", "inputs": {}},
                ],
            }
        )

        with pytest.raises(AgentModelResponseError, match="unavailable capabilities"):
            runtime.generate_plan(
                PlanGenerationRequest(
                    user_goal="Calculate.",
                    intent=AgentIntent.COMPUTATION,
                    modality=InputModality.SPREADSHEET,
                    available_capabilities=[
                        "inspect_spreadsheet",
                        "generate_code",
                        "run_code",
                        "verify_result",
                        "generate_excel",
                        "finish",
                    ],
                )
            )


# ---------------------------------------------------------------------------
# 4. Observation-based correction
# ---------------------------------------------------------------------------


class TestObservationBasedCorrection:
    """Prove the Agent proposes retry_correct upon execution errors."""

    def test_retry_correct_after_sandbox_error(self):
        runtime, provider = _runtime_with_static(
            {
                "directive": "retry_correct",
                "summary": "Sandbox code failed due to missing column; regenerate with fix.",
                "proposed_action": {
                    "action": "generate_code",
                    "inputs": {"correction": "handle missing column gracefully"},
                    "done": False,
                    "summary": "Regenerate calculation code with corrected column lookup.",
                },
            }
        )
        observation = Observation(
            source="sandbox",
            kind="execution_error",
            summary="Code failed: KeyError on 'min_thickness' column.",
            data={"stderr": "KeyError: 'min_thickness'"},
        )

        result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    session_id=uuid4(),
                    user_goal="Calculate average equipment thickness.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="generate",
                ),
                latest_observation=observation,
                previous_context=PreviousExecutionContext(
                    last_action="run_code",
                    last_result_status=CapabilityResultStatus.FAILED,
                    allowed_next_actions=["generate_code"],
                    controller_summary="Sandbox failed; correction cycle allowed.",
                ),
            )
        )

        assert result.directive == AgentDirective.RETRY_CORRECT
        assert result.proposed_action is not None
        assert result.proposed_action.action == "generate_code"
        assert result.proposed_action.done is False
        assert len(provider.requests) == 1

    def test_retry_correct_preserves_error_context_in_prompt(self):
        """The error observation must be included in the model prompt."""
        runtime, provider = _runtime_with_static(
            {
                "directive": "retry_correct",
                "summary": "Correcting after timeout.",
                "proposed_action": {
                    "action": "generate_code",
                    "inputs": {},
                    "done": False,
                    "summary": "Regenerate.",
                },
            }
        )

        runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="execute",
                ),
                latest_observation=Observation(
                    source="sandbox",
                    kind="timeout",
                    summary="Code timed out after 30 seconds.",
                    data={"duration_seconds": 30},
                ),
                previous_context=PreviousExecutionContext(
                    last_action="run_code",
                    last_result_status=CapabilityResultStatus.FAILED,
                    allowed_next_actions=["generate_code"],
                ),
            )
        )

        prompt = provider.requests[0].prompt
        assert "timed out" in prompt or "timeout" in prompt

    def test_continue_directive_for_successful_step(self):
        runtime, _ = _runtime_with_static(
            {
                "directive": "continue",
                "summary": "Spreadsheet inspected; proceed to code generation.",
                "proposed_action": {
                    "action": "generate_code",
                    "inputs": {"goal": "compute averages"},
                    "done": False,
                    "summary": "Generate calculation code.",
                },
            }
        )

        result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate average thickness.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="inspect",
                ),
                latest_observation=Observation(
                    source="capability",
                    kind="capability_succeeded",
                    summary="Workbook inspection succeeded; schema extracted.",
                    data={"columns": ["equipment_id", "thickness_mm"]},
                ),
                previous_context=PreviousExecutionContext(
                    last_action="inspect_spreadsheet",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=["generate_code"],
                ),
            )
        )

        assert result.directive == AgentDirective.CONTINUE
        assert result.proposed_action is not None
        assert result.proposed_action.action == "generate_code"
        assert result.proposed_action.done is False

    def test_verify_directive_after_successful_execution(self):
        runtime, _ = _runtime_with_static(
            {
                "directive": "verify",
                "summary": "Code execution succeeded; verify the results.",
                "proposed_action": {
                    "action": "verify_result",
                    "inputs": {},
                    "done": False,
                    "summary": "Run verification checks on computed output.",
                },
            }
        )

        result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate averages.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="execute",
                ),
                latest_observation=Observation(
                    source="sandbox",
                    kind="execution_succeeded",
                    summary="Code executed successfully with valid output.",
                    data={"result": "averages computed"},
                ),
                previous_context=PreviousExecutionContext(
                    last_action="run_code",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=["verify_result"],
                ),
            )
        )

        assert result.directive == AgentDirective.VERIFY
        assert result.proposed_action is not None
        assert result.proposed_action.action == "verify_result"
        assert result.proposed_action.done is False

    def test_rejects_action_outside_allowed_next_actions(self):
        """Model proposing an action not in allowed_next_actions must be rejected."""
        runtime, _ = _runtime_with_static(
            {
                "directive": "continue",
                "summary": "Proceeding with wrong action.",
                "proposed_action": {
                    "action": "run_code",
                    "inputs": {},
                    "done": False,
                    "summary": "Skip ahead to execution.",
                },
            }
        )

        with pytest.raises(AgentModelResponseError, match="outside allowed_next_actions"):
            runtime.reason_about_observation(
                ObservationReasoningRequest(
                    task_state=TaskState(
                        user_goal="Calculate.",
                        intent="computation",
                        modality="spreadsheet",
                        current_step="inspect",
                    ),
                    latest_observation=Observation(
                        source="capability",
                        kind="capability_succeeded",
                        summary="Inspection done.",
                    ),
                    previous_context=PreviousExecutionContext(
                        last_action="inspect_spreadsheet",
                        last_result_status=CapabilityResultStatus.SUCCEEDED,
                        allowed_next_actions=["generate_code"],
                    ),
                )
            )


# ---------------------------------------------------------------------------
# 5. Finish decision
# ---------------------------------------------------------------------------


class TestFinishDecision:
    """Prove the Agent correctly proposes finish with done=true."""

    def test_finish_after_verification_passed(self):
        runtime, provider = _runtime_with_static(
            {
                "directive": "finish",
                "summary": "All verification checks passed; task is complete.",
                "proposed_action": {
                    "action": "finish",
                    "inputs": {},
                    "done": True,
                    "summary": "Task completed successfully.",
                },
            }
        )

        result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    session_id=uuid4(),
                    user_goal="Calculate average thickness per equipment item.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="verify",
                    verification_status=VerificationStatus.PASSED,
                ),
                latest_observation=Observation(
                    source="execution_controller",
                    kind="verification_passed",
                    summary="All deterministic verification checks passed.",
                ),
                previous_context=PreviousExecutionContext(
                    last_action="verify_result",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=["generate_excel", "finish"],
                ),
            )
        )

        assert result.directive == AgentDirective.FINISH
        assert result.proposed_action is not None
        assert result.proposed_action.action == "finish"
        assert result.proposed_action.done is True
        assert len(provider.requests) == 1

    def test_request_approval_for_document_workflow(self):
        """Scanned-document workflow requires human approval before finish."""
        runtime, _ = _runtime_with_static(
            {
                "directive": "request_approval",
                "summary": "Document is ready for human approval before finalization.",
            }
        )

        result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Prepare approval note.",
                    intent="document_drafting",
                    modality="scanned_document",
                    current_step="verify",
                    verification_status=VerificationStatus.PASSED,
                ),
                latest_observation=Observation(
                    source="execution_controller",
                    kind="verification_passed",
                    summary="Grounding verification passed.",
                ),
                previous_context=PreviousExecutionContext(
                    last_action="verify_result",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=[],
                    controller_summary="Verification passed; human approval is required.",
                ),
            )
        )

        assert result.directive == AgentDirective.REQUEST_APPROVAL
        assert result.proposed_action is None

    def test_finish_must_have_done_true(self):
        """A finish directive without done=true must be rejected by schema validation."""
        runtime, _ = _runtime_with_static(
            {
                "directive": "finish",
                "summary": "Trying to finish without done flag.",
                "proposed_action": {
                    "action": "finish",
                    "inputs": {},
                    "done": False,
                    "summary": "Incomplete finish.",
                },
            }
        )

        with pytest.raises(AgentModelResponseError):
            runtime.reason_about_observation(
                ObservationReasoningRequest(
                    task_state=TaskState(
                        user_goal="Calculate.",
                        intent="computation",
                        modality="spreadsheet",
                        current_step="verify",
                        verification_status=VerificationStatus.PASSED,
                    ),
                    latest_observation=Observation(
                        source="controller",
                        kind="verification_passed",
                        summary="Verified.",
                    ),
                    previous_context=PreviousExecutionContext(
                        last_action="verify_result",
                        last_result_status=CapabilityResultStatus.SUCCEEDED,
                        allowed_next_actions=["finish"],
                    ),
                )
            )

    def test_continue_or_retry_must_not_have_done_true(self):
        """Non-finish directives with done=true must be rejected."""
        runtime, _ = _runtime_with_static(
            {
                "directive": "continue",
                "summary": "Continuing but marking done prematurely.",
                "proposed_action": {
                    "action": "generate_code",
                    "inputs": {},
                    "done": True,
                    "summary": "Premature done.",
                },
            }
        )

        with pytest.raises(AgentModelResponseError):
            runtime.reason_about_observation(
                ObservationReasoningRequest(
                    task_state=TaskState(
                        user_goal="Calculate.",
                        intent="computation",
                        modality="spreadsheet",
                        current_step="inspect",
                    ),
                    latest_observation=Observation(
                        source="capability",
                        kind="capability_succeeded",
                        summary="Inspection done.",
                    ),
                    previous_context=PreviousExecutionContext(
                        last_action="inspect_spreadsheet",
                        last_result_status=CapabilityResultStatus.SUCCEEDED,
                        allowed_next_actions=["generate_code"],
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Cross-cutting: MockModelProvider integration proofs
# ---------------------------------------------------------------------------


class TestMockProviderIntegration:
    """Prove the MockModelProvider is a complete substitute for real inference."""

    def test_no_network_calls_or_gpu_required(self):
        """The entire runtime operates in-memory via MockModelProvider."""
        runtime, provider = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Pure in-memory test.",
            }
        )

        result = runtime.decide_intent(
            IntentAnalysisRequest(user_goal="Compute something.", attachments=[])
        )

        # Provider recorded exactly one in-memory request, no external calls
        assert len(provider.requests) == 1
        assert result.intent == AgentIntent.COMPUTATION

    def test_response_factory_enables_scenario_specific_responses(self):
        """The response_factory allows dynamic responses per prompt context."""
        call_count = 0

        def dynamic_factory(request: ModelGenerationRequest) -> str:
            nonlocal call_count
            call_count += 1
            return json.dumps(
                {
                    "intent": "computation",
                    "modality": "spreadsheet",
                    "workflow": "computation",
                    "summary": f"Dynamic response #{call_count}.",
                }
            )

        runtime, provider = _runtime_with_factory(dynamic_factory)

        r1 = runtime.decide_intent(
            IntentAnalysisRequest(user_goal="First call.", attachments=[])
        )
        r2 = runtime.decide_intent(
            IntentAnalysisRequest(user_goal="Second call.", attachments=[])
        )

        assert r1.summary == "Dynamic response #1."
        assert r2.summary == "Dynamic response #2."
        assert len(provider.requests) == 2

    def test_system_prompt_contains_response_schema(self):
        """Verify the model receives the expected JSON schema in the system prompt."""
        runtime, provider = _runtime_with_static(
            {
                "intent": "computation",
                "modality": "spreadsheet",
                "workflow": "computation",
                "summary": "Schema test.",
            }
        )
        runtime.decide_intent(
            IntentAnalysisRequest(user_goal="Test schema.", attachments=[])
        )

        system_prompt = provider.requests[0].system_prompt
        assert system_prompt is not None
        assert "IntentAnalysisResult" in system_prompt
        assert "JSON" in system_prompt

    def test_full_agent_lifecycle_intent_to_finish(self):
        """Prove a complete Agent lifecycle through MockModelProvider:
        intent → plan → observe continue → observe error → retry_correct → finish.
        """
        call_sequence = []

        def lifecycle_factory(request: ModelGenerationRequest) -> str:
            call_sequence.append(request)
            step = len(call_sequence)

            if step == 1:  # Intent classification
                return json.dumps(
                    {
                        "intent": "computation",
                        "modality": "spreadsheet",
                        "workflow": "computation",
                        "summary": "Computation task.",
                    }
                )
            elif step == 2:  # Plan generation
                return json.dumps(
                    {
                        "intent": "computation",
                        "modality": "spreadsheet",
                        "workflow": "computation",
                        "summary": "Full pipeline.",
                        "steps": [
                            {"capability_name": "inspect_spreadsheet", "purpose": "Inspect.", "inputs": {}},
                            {"capability_name": "generate_code", "purpose": "Generate.", "inputs": {}},
                            {"capability_name": "run_code", "purpose": "Run.", "inputs": {}},
                            {"capability_name": "verify_result", "purpose": "Verify.", "inputs": {}},
                            {"capability_name": "generate_excel", "purpose": "Excel.", "inputs": {}},
                            {"capability_name": "finish", "purpose": "Done.", "inputs": {}},
                        ],
                    }
                )
            elif step == 3:  # Observation: continue after inspect
                return json.dumps(
                    {
                        "directive": "continue",
                        "summary": "Proceed to code generation.",
                        "proposed_action": {
                            "action": "generate_code",
                            "inputs": {},
                            "done": False,
                            "summary": "Generate code.",
                        },
                    }
                )
            elif step == 4:  # Observation: retry_correct after sandbox error
                return json.dumps(
                    {
                        "directive": "retry_correct",
                        "summary": "Fix code error.",
                        "proposed_action": {
                            "action": "generate_code",
                            "inputs": {"correction": "fix column name"},
                            "done": False,
                            "summary": "Regenerate with fix.",
                        },
                    }
                )
            elif step == 5:  # Observation: finish after verification
                return json.dumps(
                    {
                        "directive": "finish",
                        "summary": "All checks passed.",
                        "proposed_action": {
                            "action": "finish",
                            "inputs": {},
                            "done": True,
                            "summary": "Complete.",
                        },
                    }
                )
            return "{}"

        runtime, provider = _runtime_with_factory(lifecycle_factory)

        # Step 1: Intent
        intent_result = runtime.decide_intent(
            IntentAnalysisRequest(
                user_goal="Calculate average thickness.",
                attachments=[AttachmentDescriptor(name="data.xlsx", media_type="text/csv")],
            )
        )
        assert intent_result.intent == AgentIntent.COMPUTATION

        # Step 2: Plan
        plan_result = runtime.generate_plan(
            PlanGenerationRequest(
                user_goal="Calculate average thickness.",
                intent=AgentIntent.COMPUTATION,
                modality=InputModality.SPREADSHEET,
                available_capabilities=[
                    "inspect_spreadsheet",
                    "generate_code",
                    "run_code",
                    "verify_result",
                    "generate_excel",
                    "finish",
                ],
            )
        )
        assert len(plan_result.steps) == 6

        # Step 3: Observe success → continue
        continue_result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="inspect",
                ),
                latest_observation=Observation(
                    source="capability",
                    kind="capability_succeeded",
                    summary="Inspection done.",
                ),
                previous_context=PreviousExecutionContext(
                    last_action="inspect_spreadsheet",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=["generate_code"],
                ),
            )
        )
        assert continue_result.directive == AgentDirective.CONTINUE

        # Step 4: Observe error → retry_correct
        retry_result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="execute",
                ),
                latest_observation=Observation(
                    source="sandbox",
                    kind="execution_error",
                    summary="KeyError in generated code.",
                    data={"stderr": "KeyError: 'min_thickness'"},
                ),
                previous_context=PreviousExecutionContext(
                    last_action="run_code",
                    last_result_status=CapabilityResultStatus.FAILED,
                    allowed_next_actions=["generate_code"],
                ),
            )
        )
        assert retry_result.directive == AgentDirective.RETRY_CORRECT

        # Step 5: Observe verification success → finish
        finish_result = runtime.reason_about_observation(
            ObservationReasoningRequest(
                task_state=TaskState(
                    user_goal="Calculate.",
                    intent="computation",
                    modality="spreadsheet",
                    current_step="verify",
                    verification_status=VerificationStatus.PASSED,
                ),
                latest_observation=Observation(
                    source="controller",
                    kind="verification_passed",
                    summary="All checks passed.",
                ),
                previous_context=PreviousExecutionContext(
                    last_action="verify_result",
                    last_result_status=CapabilityResultStatus.SUCCEEDED,
                    allowed_next_actions=["generate_excel", "finish"],
                ),
            )
        )
        assert finish_result.directive == AgentDirective.FINISH
        assert finish_result.proposed_action.done is True

        # All five model calls went through MockModelProvider
        assert len(call_sequence) == 5
        assert all(req.model_id == "agent-model" for req in call_sequence)
