"""DraftApprovalNote capability: routes approval note drafting to the agent model role.

Drafts a structured approval note summarizing:
- Key extracted facts and findings
- Supporting observations
- Recommended actions
- Approval status placeholder

Strictly keeps extracted facts separate from recommendations and does not expose chain-of-thought.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from aegis.events import (
    ExecutionEvent,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.router import (
    ModelGenerationRequest,
    ModelProvider,
    ModelRouter,
    RoutingDecision,
)
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
)


def _build_drafting_prompt(user_goal: str, extracted_text: str, document_title: str) -> str:
    return (
        f"Review the following inspection document "
        f"and draft an executive approval note in response to the user's objective.\n\n"
        f"USER OBJECTIVE:\n{user_goal}\n\n"
        f"DOCUMENT TITLE: {document_title}\n\n"
        f"EXTRACTED DOCUMENT TEXT:\n{extracted_text}\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. Clearly separate extracted facts/findings from recommendations.\n"
        f"2. Present supporting observations based strictly on the extracted text.\n"
        f"3. Return ONLY valid JSON with no markdown fences, no conversational text, and no chain-of-thought.\n"
        f"4. The JSON must follow this exact schema:\n"
        f"{{\n"
        f'  "title": "APPROVAL NOTE: ...",\n'
        f'  "document_reference": "{document_title}",\n'
        f'  "approval_status": "DRAFT — PENDING OPERATOR APPROVAL",\n'
        f'  "key_findings": [\n'
        f'    "Fact 1...",\n'
        f'    "Fact 2..."\n'
        f"  ],\n"
        f'  "supporting_observations": [\n'
        f'    "Observation 1...",\n'
        f'    "Observation 2..."\n'
        f"  ],\n"
        f'  "recommendations": [\n'
        f'    "Recommendation 1...",\n'
        f'    "Recommendation 2..."\n'
        f"  ],\n"
        f'  "summary": "Brief executive summary of findings and proposed action."\n'
        f"}}\n"
    )


def _parse_draft_json(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


class DraftApprovalNoteCapability(Capability):
    """Draft approval-note content using the configured agent-role model."""

    def __init__(
        self,
        router: ModelRouter,
        providers: dict[str, ModelProvider],
        event_publisher: ExecutionEventPublisher | None = None,
    ) -> None:
        self._router = router
        self._providers = dict(providers)
        self._event_publisher = event_publisher
        self._metadata = CapabilityMetadata(
            name="draft_approval_note",
            kind=CapabilityKind.MODEL,
            description="Draft a structured approval note using the agent-role model.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "extracted_text": {"type": "string"},
                        "user_goal": {"type": "string"},
                        "document_title": {"type": "string"},
                    },
                    "required": ["extracted_text"],
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "key_findings": {"type": "array", "items": {"type": "string"}},
                        "supporting_observations": {"type": "array", "items": {"type": "string"}},
                        "recommendations": {"type": "array", "items": {"type": "string"}},
                        "approval_status": {"type": "string"},
                    },
                    "required": ["title", "key_findings", "supporting_observations", "recommendations"],
                }
            ),
            input_modalities=("document", "scanned_document"),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        extracted_text = request.inputs.get("extracted_text") or request.inputs.get("text", "")
        user_goal = request.inputs.get("user_goal", "Draft an approval note based on the attached report.")
        doc_title = request.inputs.get("document_title", "Inspection Report")

        if not isinstance(extracted_text, str) or not extracted_text.strip():
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Cannot draft approval note without extracted document text.",
            )

        # Route to agent-role model
        try:
            routing: RoutingDecision = self._router.route(
                task_type="drafting",
                modality="document",
                required_capability="drafting",
            )
        except Exception as exc:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Model routing for drafting failed: {exc}",
            )

        provider = self._providers.get(routing.provider_id)
        if provider is None:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Provider '{routing.provider_id}' is not configured for drafting.",
            )

        prompt = _build_drafting_prompt(user_goal, extracted_text, doc_title)
        model_req = ModelGenerationRequest(
            model_id=routing.model_id,
            system_prompt=(
                "You are the AEGIS AI Approval Note Specialist. "
                "Review inspection documents and draft structured executive approval notes. "
                "Output JSON only."
            ),
            prompt=prompt,
        )

        try:
            generation = provider.generate(model_req)
        except Exception as exc:
            if self._event_publisher is not None:
                self._event_publisher.publish(
                    ExecutionEvent(
                        session_id=request.task_id or uuid.uuid4(),
                        task_id=request.task_id or uuid.uuid4(),
                        user_id=None,
                        event_type=ExecutionEventType.MODEL_INVOKED,
                        component="draft_approval_note",
                        status=ExecutionEventStatus.FAILED,
                        summary=f"Model invocation failed for {routing.model_id}.",
                        capability_id="draft_approval_note",
                        model_id=routing.model_id,
                        model_provider_id=routing.provider_id,
                        request_id=request.request_id,
                        metadata={
                            "prompt": model_req.prompt,
                            "system_prompt": model_req.system_prompt,
                            "model_prompt": model_req.prompt,
                            "role": "agent",
                            "task_type": "drafting",
                            "error": str(exc),
                        },
                    )
                )
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Model generation failed: {exc}",
            )

        if self._event_publisher is not None:
            self._event_publisher.publish(
                ExecutionEvent(
                    session_id=request.task_id or uuid.uuid4(),
                    task_id=request.task_id or uuid.uuid4(),
                    user_id=None,
                    event_type=ExecutionEventType.MODEL_INVOKED,
                    component="draft_approval_note",
                    status=ExecutionEventStatus.COMPLETED,
                    summary=f"Model invocation completed for {routing.model_id}.",
                    capability_id="draft_approval_note",
                    model_id=routing.model_id,
                    model_provider_id=routing.provider_id,
                    request_id=request.request_id,
                    metadata={
                        "prompt": model_req.prompt,
                        "system_prompt": model_req.system_prompt,
                        "model_prompt": model_req.prompt,
                        "model_raw_response": generation.text,
                        "role": "agent",
                        "task_type": "drafting",
                    },
                )
            )

        parsed = _parse_draft_json(generation.text)
        if not parsed:
            # Fallback deterministic structured extraction if model returned non-JSON
            parsed = {
                "title": f"APPROVAL NOTE: {doc_title}",
                "document_reference": doc_title,
                "approval_status": "DRAFT — PENDING OPERATOR APPROVAL",
                "key_findings": [f"Document reviewed: {doc_title}"],
                "supporting_observations": [generation.text.strip()],
                "recommendations": ["Operator review required before finalization."],
                "summary": "Draft approval note generated from inspection report.",
            }

        # Enforce separation of sections
        if "key_findings" not in parsed:
            parsed["key_findings"] = []
        if "supporting_observations" not in parsed:
            parsed["supporting_observations"] = []
        if "recommendations" not in parsed:
            parsed["recommendations"] = []
        if "approval_status" not in parsed:
            parsed["approval_status"] = "DRAFT — PENDING OPERATOR APPROVAL"

        obs = Observation(
            source="draft_approval_note",
            kind="approval_note_drafted",
            summary=f"Drafted approval note '{parsed.get('title')}' with {len(parsed['key_findings'])} findings.",
            data={
                "title": parsed.get("title"),
                "findings_count": len(parsed["key_findings"]),
                "observations_count": len(parsed["supporting_observations"]),
                "recommendations_count": len(parsed["recommendations"]),
                "model_id": routing.model_id,
                "model_prompt": model_req.prompt,
                "model_raw_response": generation.text,
            },
            request_id=request.request_id,
        )

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output=parsed,
            observations=[obs],
        )
