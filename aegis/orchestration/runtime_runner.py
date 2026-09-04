"""Real AEGIS runtime task runner.

Executes requests through the real AEGIS runtime:
Request
→ RouterAgentRuntime
→ intent/modality/workflow decision
→ ExecutionController
→ CapabilityBroker
→ inspect_spreadsheet
→ coding-role model / generate_code
→ run_code
→ verify_result
→ generate_excel
→ Artifact

Enforces all architectural invariants:
- The Agent/model determines the required workflow without keyword matching.
- Deterministic capabilities remain deterministic.
- Code generation uses the coding model via ModelRouter.
- Code execution uses the isolated sandbox mechanism (Docker or MockSandboxRunner).
- Verification remains deterministic.
- Zero exposure of chain-of-thought.
- Preserves execution events and session/task/user identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from aegis.agent.runtime import RouterAgentRuntime
from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    CapabilityRegistry,
    DockerSandboxRunner,
    DraftApprovalNoteCapability,
    ExtractDocumentCapability,
    FinishCapability,
    GenerateCodeCapability,
    GenerateExcelCapability,
    GenerateWordCapability,
    InspectSpreadsheetCapability,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxRunner,
    VerifyResultCapability,
)
from aegis.config import AegisConfig, load_config
from aegis.data import DocumentCategory, identify_document_type
from aegis.events import (
    ExecutionEvent,
    ExecutionEventContext,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.orchestration.controller import ExecutionController
from aegis.orchestration.hitl import HITLApprovalState
from aegis.orchestration.workflows import WorkflowName
from aegis.router import ModelProvider, ModelRegistry, ModelRouter
from aegis.schemas import (
    AgentDecision,
    CapabilityResultStatus,
    FinalStatus,
    TaskState,
    VerificationStatus,
)
from aegis.skills import (
    ComputationContext,
    build_code_generation_prompt,
    prepare_generate_code_inputs,
    prepare_run_code_inputs,
)


@dataclass(frozen=True)
class ExecutionRunResult:
    """Outcome of running or resuming a task execution."""

    task_id: UUID
    session_id: UUID
    workflow_id: str
    final_status: FinalStatus
    hitl_state: HITLApprovalState | None
    events: list[ExecutionEvent]
    result_text: str
    artifact_paths: list[str]


def _infer_media_type(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".xlsx", ".xls"}:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext in {".csv"}:
        return "text/csv"
    if ext in {".pdf"}:
        return "application/pdf"
    if ext in {".png"}:
        return "image/png"
    return "application/octet-stream"


def _derive_expected_fields(
    inspection_output: dict[str, Any] | None, user_goal: str = ""
) -> list[str]:
    """Derive expected output fields from inspected spreadsheet columns and the user objective."""
    if not inspection_output:
        return []

    from aegis.capabilities.verify_result import _normalize_key

    cols: list[str] = [str(c) for c in inspection_output.get("columns", []) if c]
    if not cols:
        return []

    goal_norm = _normalize_key(user_goal)
    relevant_cols: list[str] = []
    for c in cols:
        norm_c = _normalize_key(c)
        if not norm_c:
            continue
        tokens = [t for t in norm_c.split("_") if len(t) >= 4]
        if norm_c in goal_norm or (tokens and any(t in goal_norm for t in tokens)):
            relevant_cols.append(norm_c)

    if relevant_cols:
        return relevant_cols

    return [_normalize_key(c) for c in cols[:5] if _normalize_key(c)]


def _format_computation_deliverable_text(
    records: list[dict[str, Any]],
    verification_summary: str,
    deliverable_paths: list[str],
) -> str:
    """Produce clean, user-facing computation findings without chain-of-thought."""
    below_min: list[dict[str, Any]] = []
    lines: list[str] = ["### Industrial Inspection Computation Deliverable\n"]

    if records:
        lines.append("**Equipment Thickness Summary:**")
        for rec in records:
            eq_id = rec.get("equipment_id", "Unknown")
            avg_th = rec.get("average_measured_thickness")
            min_th = rec.get("min_acceptable_thickness")
            is_below = rec.get("below_min_acceptable_thickness", False)
            if is_below:
                below_min.append(rec)
            status_flag = "[BELOW MINIMUM]" if is_below else "[OK]"
            lines.append(
                f"- **{eq_id}**: Average Measured = **{avg_th} mm** (Min Acceptable = {min_th} mm) — {status_flag}"
            )

        lines.append("")
        if below_min:
            flagged = ", ".join(f"**{r.get('equipment_id')}**" for r in below_min)
            lines.append(f"**Action Required**: Equipment falling below acceptable threshold: {flagged}.")
        else:
            lines.append("All inspected equipment items meet or exceed minimum acceptable thickness.")

    lines.append(f"\n- **Verification**: **{verification_summary.upper()}**")
    if deliverable_paths:
        lines.append(f"- **Generated Deliverable**: `{deliverable_paths[0]}`")

    return "\n".join(lines)


def _format_approval_note_deliverable_text(
    draft_data: dict[str, Any],
    verification_summary: str,
    deliverable_paths: list[str],
    approval_status: str = "WAITING FOR OPERATOR APPROVAL",
) -> str:
    """Produce clean, user-facing approval note text clearly distinguishing sections."""
    lines: list[str] = ["### Industrial Inspection Approval Note Deliverable\n"]

    title = draft_data.get("title", "APPROVAL NOTE")
    lines.append(f"**{title}**\n")

    summary = draft_data.get("summary")
    if summary:
        lines.append(f"*{summary}*\n")

    findings = draft_data.get("key_findings", [])
    if findings:
        lines.append("#### 1. Extracted Facts & Key Findings")
        for item in findings:
            lines.append(f"- {item}")
        lines.append("")

    observations = draft_data.get("supporting_observations", [])
    if observations:
        lines.append("#### 2. Supporting Observations")
        for item in observations:
            lines.append(f"- {item}")
        lines.append("")

    recommendations = draft_data.get("recommendations", [])
    if recommendations:
        lines.append("#### 3. Recommended Actions")
        for item in recommendations:
            lines.append(f"- [Action Required] {item}")
        lines.append("")

    lines.append("---")
    lines.append(f"- **Governance Status**: **{approval_status}**")
    lines.append(f"- **Deterministic Verification**: **{verification_summary.upper()}**")
    if deliverable_paths:
        lines.append(f"- **Generated Document Artifact**: `{deliverable_paths[0]}`")

    return "\n".join(lines)


class RuntimeTaskRunner:
    """Executes requests through the real AEGIS runtime stack."""

    def __init__(
        self,
        event_publisher: ExecutionEventPublisher | None = None,
        agent_runtime: RouterAgentRuntime | None = None,
        router: ModelRouter | None = None,
        providers: dict[str, ModelProvider] | None = None,
        capability_registry: CapabilityRegistry | None = None,
        sandbox_runner: SandboxRunner | None = None,
        deliverables_dir: Path | str | None = None,
        config: AegisConfig | None = None,
        event_pace_seconds: float = 0.0,
    ) -> None:
        self._config = config or load_config()
        self._publisher = event_publisher or ExecutionEventPublisher()
        self._event_pace = event_pace_seconds
        self._active_controllers: dict[UUID, ExecutionController] = {}
        self._deliverables: dict[UUID, tuple[str, list[str]]] = {}

        # Model routing & providers
        self._providers = dict(providers or {})
        self._router = router or ModelRouter(ModelRegistry(self._config.models))

        # Agent Runtime
        if agent_runtime is not None:
            self._agent_runtime = agent_runtime
        else:
            from aegis.agent.runtime import RouterAgentRuntime

            self._agent_runtime = RouterAgentRuntime(
                self._config.agent,
                self._router,
                self._providers,
                event_publisher=self._publisher,
            )

        # Sandbox Runner
        self._sandbox_runner = sandbox_runner or DockerSandboxRunner()

        # Output directory for deliverables
        self._deliverables_dir = Path(deliverables_dir or "deliverables")

        # Capabilities Registry
        if capability_registry is not None:
            self._registry = capability_registry
        else:
            self._registry = CapabilityRegistry(self._config.capabilities)
            self._registry.register(InspectSpreadsheetCapability())
            self._registry.register(
                GenerateCodeCapability(
                    router=self._router,
                    providers=self._providers,
                    event_publisher=self._publisher,
                )
            )
            self._registry.register(RunCodeCapability(sandbox=self._sandbox_runner))
            self._registry.register(VerifyResultCapability())
            self._registry.register(
                GenerateExcelCapability(output_dir=self._deliverables_dir)
            )
            self._registry.register(ExtractDocumentCapability())
            self._registry.register(
                DraftApprovalNoteCapability(
                    router=self._router,
                    providers=self._providers,
                    event_publisher=self._publisher,
                )
            )
            self._registry.register(
                GenerateWordCapability(output_dir=self._deliverables_dir)
            )
            self._registry.register(FinishCapability())

    @property
    def event_publisher(self) -> ExecutionEventPublisher:
        return self._publisher

    def get_controller(self, task_id: UUID) -> ExecutionController | None:
        """Return the active ExecutionController for a task, if any."""
        return self._active_controllers.get(task_id)

    def _events_for_task(self, task_id: UUID) -> list[ExecutionEvent]:
        return [ev for ev in self._publisher.events if ev.task_id == task_id]

    def record_approval(
        self,
        task_id: UUID,
        user_id: str,
        approved: bool,
    ) -> ExecutionRunResult:
        """Record human approval/rejection for an awaiting task and continue."""
        controller = self._active_controllers.get(task_id)
        if controller is None:
            raise ValueError(f"No active controller found for task {task_id}")

        event = controller.record_approval(approved=approved, user_id=user_id)
        if event.status == ExecutionEventStatus.REJECTED:
            raise ValueError(f"Approval transition rejected by Controller: {event.summary}")

        if not approved:
            result_text = "Task was rejected by operator.\n\n[Status: REJECTED]"
            self._deliverables[task_id] = (result_text, [])
            return ExecutionRunResult(
                task_id=task_id,
                session_id=controller.state.session_id,
                workflow_id=controller.workflow.name.value,
                final_status=controller.state.final_status,
                hitl_state=controller.hitl_state,
                events=list(controller.execution_events),
                result_text=result_text,
                artifact_paths=[],
            )

        controller.execute(AgentDecision(action="finish", done=True))
        result_text, artifacts = self._deliverables.get(
            task_id,
            ("Workflow completed successfully.", []),
        )
        result_text += "\n\n[Status: APPROVED and FINALIZED]"
        return ExecutionRunResult(
            task_id=task_id,
            session_id=controller.state.session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifacts,
        )

    def start_execution(
        self,
        session_id: UUID,
        task_id: UUID,
        user_id: str,
        user_goal: str,
        attachment_path: str | None = None,
    ) -> ExecutionRunResult:
        """Execute request through the real AEGIS runtime."""
        context = ExecutionEventContext(
            session_id=session_id,
            task_id=task_id,
            user_id=user_id,
        )

        from aegis.agent.schemas import AttachmentDescriptor, IntentAnalysisRequest

        # 1. Deterministic document type identification if attachment present
        attachments: list[AttachmentDescriptor] = []
        if attachment_path:
            att_file = Path(attachment_path)
            if att_file.exists():
                try:
                    doc_ident = identify_document_type(att_file)
                    self._emit(
                        context,
                        ExecutionEventType.DOCUMENT_TYPE_IDENTIFIED,
                        ExecutionEventStatus.COMPLETED,
                        f"Identified document type '{doc_ident.category.value}' ({doc_ident.mime_type}).",
                        metadata={
                            "category": doc_ident.category.value,
                            "mime_type": doc_ident.mime_type,
                            "detection_method": doc_ident.detection_method,
                            "has_extractable_text": doc_ident.has_extractable_text,
                            "page_count": doc_ident.page_count,
                        },
                    )
                    attachments.append(
                        AttachmentDescriptor(
                            name=att_file.name,
                            media_type=doc_ident.mime_type,
                            document_type=doc_ident.category.value,
                            has_extractable_text=doc_ident.has_extractable_text,
                            page_count=doc_ident.page_count,
                        )
                    )
                except Exception:
                    attachments.append(
                        AttachmentDescriptor(
                            name=att_file.name,
                            media_type=_infer_media_type(attachment_path),
                        )
                    )
            else:
                attachments.append(
                    AttachmentDescriptor(
                        name=Path(attachment_path).name,
                        media_type=_infer_media_type(attachment_path),
                    )
                )

        # 2. Agent Runtime determines intent, modality, and workflow via Model
        # (NO keyword matching!)
        try:
            intent_result = self._agent_runtime.decide_intent(
                IntentAnalysisRequest(
                    user_goal=user_goal,
                    attachments=attachments,
                    event_context=context,
                )
            )
            workflow_name = intent_result.workflow
        except Exception as exc:
            self._emit(
                context,
                ExecutionEventType.TASK_STARTED,
                ExecutionEventStatus.STARTED,
                "Task execution started.",
            )
            failed_event = self._emit(
                context,
                ExecutionEventType.TASK_FAILED,
                ExecutionEventStatus.FAILED,
                f"Agent could not determine workflow intent: {exc}",
                metadata={"error": str(exc)},
            )
            return ExecutionRunResult(
                task_id=task_id,
                session_id=session_id,
                workflow_id="none",
                final_status=FinalStatus.FAILED,
                hitl_state=None,
                events=self._events_for_task(task_id),
                result_text=f"### Request Analysis Error\n\nCould not determine supported intent: {exc}",
                artifact_paths=[],
            )

        # 3. Guard: Supported workflows in real runtime
        if workflow_name not in (WorkflowName.COMPUTATION, WorkflowName.SCANNED_DOCUMENT_APPROVAL):
            self._emit(
                context,
                ExecutionEventType.TASK_STARTED,
                ExecutionEventStatus.STARTED,
                "Task execution started.",
            )
            failed_event = self._emit(
                context,
                ExecutionEventType.TASK_FAILED,
                ExecutionEventStatus.FAILED,
                f"Workflow '{workflow_name.value}' is not implemented in this slice.",
                metadata={"workflow": workflow_name.value},
            )
            return ExecutionRunResult(
                task_id=task_id,
                session_id=session_id,
                workflow_id=workflow_name.value,
                final_status=FinalStatus.FAILED,
                hitl_state=None,
                events=self._events_for_task(task_id),
                result_text=(
                    f"### Workflow Unavailable\n\n"
                    f"The request resolved to intent `{intent_result.intent.value}` and workflow `{workflow_name.value}`. "
                    "In this phase, only computation and document drafting workflows are implemented in the real runtime."
                ),
                artifact_paths=[],
            )

        # Guard: Requires an existing attachment on disk
        if not attachment_path or not Path(attachment_path).exists():
            self._emit(
                context,
                ExecutionEventType.TASK_STARTED,
                ExecutionEventStatus.STARTED,
                "Task execution started.",
            )
            failed_event = self._emit(
                context,
                ExecutionEventType.TASK_FAILED,
                ExecutionEventStatus.FAILED,
                f"Workflow requires an attachment, but file is missing or does not exist on disk.",
            )
            return ExecutionRunResult(
                task_id=task_id,
                session_id=session_id,
                workflow_id=workflow_name.value,
                final_status=FinalStatus.FAILED,
                hitl_state=None,
                events=self._events_for_task(task_id),
                result_text="### Missing Attachment\n\nThe requested workflow requires an existing file attachment.",
                artifact_paths=[],
            )

        # 4. Initialize authoritative TaskState and ExecutionController
        task_state = TaskState(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            user_goal=user_goal,
            attachments=[str(attachment_path)],
            selected_skill=workflow_name.value,
            intent=intent_result.intent.value,
            modality=intent_result.modality.value,
            max_retries=2,
            max_iterations=8,
        )

        broker = RegistryCapabilityBroker(self._registry)
        controller = ExecutionController(
            state=task_state,
            workflow=workflow_name,
            broker=broker,
            event_publisher=self._publisher,
        )
        self._active_controllers[task_id] = controller

        if workflow_name == WorkflowName.COMPUTATION:
            return self._execute_computation_workflow(controller, attachment_path)
        else:
            return self._execute_document_drafting_workflow(controller, attachment_path)

    def _execute_computation_workflow(
        self,
        controller: ExecutionController,
        attachment_path: str,
    ) -> ExecutionRunResult:
        """Run inspect_spreadsheet -> generate_code -> run_code -> verify_result -> generate_excel -> finish."""
        task_id = controller.state.task_id
        session_id = controller.state.session_id
        user_goal = controller.state.user_goal

        # Step 1: inspect_spreadsheet
        controller.execute(
            AgentDecision(
                action="inspect_spreadsheet",
                inputs={"workbook": attachment_path},
            )
        )
        inspection = controller.last_capability_result
        if inspection is None or inspection.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Spreadsheet inspection failed.")

        # Step 2: Formulate prompt and generate code through coding model
        context = ComputationContext(
            user_goal=user_goal,
            file_path=attachment_path,
            sheet_names=inspection.output.get("sheet_names", []),
            columns=inspection.output.get("columns", []),
            numeric_fields=inspection.output.get("numeric_fields", []),
            row_count=inspection.output.get("row_count", 0),
            representative_values=inspection.output.get("representative_values", {}),
        )
        formulation = build_code_generation_prompt(context)
        controller.execute(
            AgentDecision(
                action="generate_code",
                inputs=prepare_generate_code_inputs(formulation),
            )
        )
        generation = controller.last_capability_result
        if generation is None or generation.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Code generation failed.")

        code = generation.output.get("code")
        if not isinstance(code, str) or not code.strip():
            return self._build_result(controller, "No executable code generated.")

        # Step 3: run_code in sandbox
        run_event = controller.execute(
            AgentDecision(
                action="run_code",
                inputs=prepare_run_code_inputs(code, attachment_path),
            )
        )

        successful_run = controller.last_capability_result
        # If sandbox execution failed, enter bounded error recovery loop
        if (
            run_event.status == ExecutionEventStatus.FAILED
            or successful_run is None
            or successful_run.status != CapabilityResultStatus.SUCCEEDED
        ):
            from aegis.agent.sandbox_feedback import SandboxObservationLoop

            loop = SandboxObservationLoop(self._agent_runtime, controller)
            recovery = loop.recover_from_run_code_failure(
                context, code, data_file_path=attachment_path
            )
            successful_run = controller.last_capability_result

        if successful_run is None or successful_run.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Sandbox execution could not be completed.")

        # Step 4: verify_result (deterministic verification)
        stdout = successful_run.output.get("stdout", "")
        stderr = successful_run.output.get("stderr", "")
        exit_code = successful_run.output.get("exit_code", 0)
        timed_out = successful_run.output.get("timed_out", False)

        derived_fields = _derive_expected_fields(
            inspection.output if inspection else None,
            user_goal=user_goal,
        )

        verification_inputs = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "computation_objective": user_goal,
            "expected_fields": derived_fields,
            "min_row_count": 1,
        }
        controller.execute(
            AgentDecision(
                action="verify_result",
                inputs=verification_inputs,
            )
        )
        verification = controller.last_capability_result
        if (
            verification is None
            or verification.status != CapabilityResultStatus.SUCCEEDED
            or controller.state.verification_status != VerificationStatus.PASSED
        ):
            return self._build_result(controller, "Computation output failed deterministic verification.")

        # Step 5: generate_excel (produce verified deliverable)
        verification_summary = verification.output.get("summary", "PASSED")
        excel_inputs = {
            "requested_calculation": user_goal,
            "source_data_reference": attachment_path,
            "stdout": stdout,
            "methodology": f"Automated computation fulfilling objective: {user_goal}",
            "verification_status": verification_summary,
        }
        controller.execute(
            AgentDecision(
                action="generate_excel",
                inputs=excel_inputs,
            )
        )
        excel_result = controller.last_capability_result
        if excel_result is None or excel_result.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Excel deliverable generation failed.")

        # Step 6: finish (complete governed workflow)
        controller.execute(AgentDecision(action="finish", done=True))

        # 7. Formulate final deliverable text and artifacts
        artifact_paths = [a.location for a in controller.state.generated_artifacts]
        parsed_records: list[dict[str, Any]] = []
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, list):
                parsed_records = parsed
        except Exception:
            pass

        result_text = _format_computation_deliverable_text(
            parsed_records,
            verification_summary,
            artifact_paths,
        )
        self._deliverables[task_id] = (result_text, artifact_paths)

        return ExecutionRunResult(
            task_id=task_id,
            session_id=session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifact_paths,
        )

    def _execute_document_drafting_workflow(
        self,
        controller: ExecutionController,
        attachment_path: str,
    ) -> ExecutionRunResult:
        """Run extract_document -> draft_approval_note -> generate_word -> verify_result -> HITL pause."""
        task_id = controller.state.task_id
        session_id = controller.state.session_id
        user_goal = controller.state.user_goal

        # Step 1: extract_document (deterministic text extraction from PDF)
        controller.execute(
            AgentDecision(
                action="extract_document",
                inputs={"document_path": attachment_path},
            )
        )
        extraction = controller.last_capability_result
        if extraction is None or extraction.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Document extraction failed.")

        extracted_text = extraction.output.get("text", "")
        doc_title = extraction.output.get("title") or Path(attachment_path).stem

        # Step 2: draft_approval_note (agent-role model drafts structured sections)
        controller.execute(
            AgentDecision(
                action="draft_approval_note",
                inputs={
                    "extracted_text": extracted_text,
                    "user_goal": user_goal,
                    "document_title": doc_title,
                },
            )
        )
        drafting = controller.last_capability_result
        if drafting is None or drafting.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Approval note drafting failed.")

        draft_data = drafting.output

        # Step 3: generate_word (produce formatted DOCX deliverable)
        controller.execute(
            AgentDecision(
                action="generate_word",
                inputs={
                    "title": draft_data.get("title", f"APPROVAL NOTE: {doc_title}"),
                    "document_reference": doc_title,
                    "key_findings": draft_data.get("key_findings", []),
                    "supporting_observations": draft_data.get("supporting_observations", []),
                    "recommendations": draft_data.get("recommendations", []),
                    "approval_status": "DRAFT — PENDING OPERATOR APPROVAL",
                    "summary": draft_data.get("summary", ""),
                },
            )
        )
        word_result = controller.last_capability_result
        if word_result is None or word_result.status != CapabilityResultStatus.SUCCEEDED:
            return self._build_result(controller, "Word document deliverable generation failed.")

        docx_path = word_result.output.get("file_path")

        # Step 4: verify_result (deterministic verification of structure and separation)
        controller.execute(
            AgentDecision(
                action="verify_result",
                inputs={
                    "draft_data": draft_data,
                    "docx_path": docx_path,
                },
            )
        )
        verification = controller.last_capability_result
        if (
            verification is None
            or verification.status != CapabilityResultStatus.SUCCEEDED
            or controller.state.verification_status != VerificationStatus.PASSED
        ):
            return self._build_result(controller, "Approval note output failed deterministic verification.")

        # Note: In SCANNED_DOCUMENT_APPROVAL_WORKFLOW, verify_result advances the
        # Controller HITL state machine to WAITING_FOR_APPROVAL and requires human approval
        # before the workflow can execute 'finish'.
        artifact_paths = [a.location for a in controller.state.generated_artifacts]
        verification_summary = verification.output.get("summary", "PASSED")

        result_text = _format_approval_note_deliverable_text(
            draft_data,
            verification_summary,
            artifact_paths,
            approval_status="WAITING FOR OPERATOR APPROVAL (HITL GATE)",
        )
        self._deliverables[task_id] = (result_text, artifact_paths)

        return ExecutionRunResult(
            task_id=task_id,
            session_id=session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifact_paths,
        )

    def _build_result(
        self, controller: ExecutionController, error_msg: str
    ) -> ExecutionRunResult:
        artifact_paths = [a.location for a in controller.state.generated_artifacts]
        workflow_label = controller.workflow.name.value.replace("_", " ").title()
        result_text = f"### {workflow_label} Workflow Failed\n\n{error_msg}"
        return ExecutionRunResult(
            task_id=controller.state.task_id,
            session_id=controller.state.session_id,
            workflow_id=controller.workflow.name.value,
            final_status=controller.state.final_status,
            hitl_state=controller.hitl_state,
            events=list(controller.execution_events),
            result_text=result_text,
            artifact_paths=artifact_paths,
        )

    def _emit(
        self,
        context: ExecutionEventContext,
        event_type: ExecutionEventType,
        status: ExecutionEventStatus,
        summary: str,
        metadata: dict[str, object] | None = None,
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            session_id=context.session_id,
            task_id=context.task_id,
            user_id=context.user_id,
            event_type=event_type,
            component="runtime_runner",
            status=status,
            summary=summary,
            metadata=metadata or {},
        )
        self._publisher.publish(event)
        return event
