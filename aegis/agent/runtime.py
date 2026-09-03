"""Router-backed Agent Runtime that proposes structured decisions without executing them."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aegis.config import AgentConfig
from aegis.events import (
    ExecutionEvent,
    ExecutionEventContext,
    ExecutionEventPublisher,
    ExecutionEventStatus,
    ExecutionEventType,
)
from aegis.router import ModelGenerationRequest, ModelProvider, ModelRouter, RoutingDecision

from .schemas import (
    AgentDirective,
    IntentAnalysisRequest,
    IntentAnalysisResult,
    ObservationDecision,
    ObservationReasoningRequest,
    PlanGenerationRequest,
    PlanProposal,
    workflow_for_intent,
)

_MODEL_RESPONSE = TypeVar("_MODEL_RESPONSE", bound=BaseModel)


class AgentRuntimeError(RuntimeError):
    """Base error for Agent Runtime failures."""


class AgentProviderResolutionError(AgentRuntimeError):
    """Raised when router-selected provider instances are unavailable."""


class AgentModelResponseError(AgentRuntimeError, ValueError):
    """Raised when the model response is not valid structured Agent output."""


class AgentRuntime(ABC):
    """Provider-neutral Agent Runtime contract for structured proposal generation."""

    @abstractmethod
    def decide_intent(self, request: IntentAnalysisRequest) -> IntentAnalysisResult:
        """Return structured intent and modality analysis."""

    @abstractmethod
    def generate_plan(self, request: PlanGenerationRequest) -> PlanProposal:
        """Return a bounded capability plan for Controller validation."""

    @abstractmethod
    def reason_about_observation(
        self, request: ObservationReasoningRequest
    ) -> ObservationDecision:
        """Return a structured next-step decision from the latest observation."""


class RouterAgentRuntime(AgentRuntime):
    """Structured Agent Runtime that reaches models only via Router -> ModelProvider."""

    def __init__(
        self,
        config: AgentConfig,
        router: ModelRouter,
        providers: dict[str, ModelProvider],
        event_publisher: ExecutionEventPublisher | None = None,
    ) -> None:
        self._config = config
        self._router = router
        self._providers = dict(providers)
        self._event_publisher = event_publisher

    def decide_intent(self, request: IntentAnalysisRequest) -> IntentAnalysisResult:
        result = self._generate_structured(
            task_type="general_reasoning",
            modality_hint=self._infer_router_modality(request.attachments),
            required_capability="reasoning",
            response_model=IntentAnalysisResult,
            instruction=(
                "Classify the request into the prototype's supported intent and input modality. "
                "Choose exactly one intent and one modality. "
                "Use scanned_document for scanned PDFs or reports that require OCR, "
                "spreadsheet for workbook-style/tabular inputs, and image for photo-based visual analysis."
            ),
            payload=request,
            event_context=request.event_context,
        )

        if result.modality.value not in set(self._config.allowed_modalities):
            raise AgentModelResponseError(
                f"Model selected unsupported modality '{result.modality.value}'."
            )
        self._emit(
            request.event_context,
            ExecutionEventType.INTENT_IDENTIFIED,
            ExecutionEventStatus.COMPLETED,
            result.summary,
            metadata={"intent": result.intent.value, "modality": result.modality.value},
        )
        return result

    def generate_plan(self, request: PlanGenerationRequest) -> PlanProposal:
        result = self._generate_structured(
            task_type="general_reasoning",
            modality_hint=request.modality.value,
            required_capability="planning",
            response_model=PlanProposal,
            instruction=(
                "Propose a bounded capability plan for the Controller. "
                "Use only available capabilities, keep the sequence Controller-compatible, "
                "and do not invent tool calls or execution side effects."
            ),
            payload=request,
            event_context=request.event_context,
        )
        self._validate_plan(result, request)
        return result

    def reason_about_observation(
        self, request: ObservationReasoningRequest
    ) -> ObservationDecision:
        result = self._generate_structured(
            task_type="general_reasoning",
            modality_hint=request.task_state.modality,
            required_capability="reasoning",
            response_model=ObservationDecision,
            instruction=(
                "Return one structured next-step decision for the Controller. "
                "Do not reveal chain-of-thought. "
                "Use request_approval only when the task is waiting for human approval, "
                "verify only for verify_result, and finish only for action finish with done=true."
            ),
            payload=request,
            event_context=ExecutionEventContext(
                session_id=request.task_state.session_id,
                task_id=request.task_state.task_id,
                user_id=request.task_state.user_id,
            ),
        )
        self._validate_observation_decision(result, request)
        return result

    def _generate_structured(
        self,
        *,
        task_type: str,
        modality_hint: str | None,
        required_capability: str,
        response_model: type[_MODEL_RESPONSE],
        instruction: str,
        payload: BaseModel,
        event_context: ExecutionEventContext | None,
    ) -> _MODEL_RESPONSE:
        routing = self._router.route(
            task_type,
            modality=modality_hint,
            required_capability=required_capability,
        )
        self._emit(
            event_context,
            ExecutionEventType.MODEL_SELECTED,
            ExecutionEventStatus.COMPLETED,
            f"Selected model {routing.model_id} for {task_type}.",
            model_id=routing.model_id,
            model_provider_id=routing.provider_id,
            metadata={"role": routing.role, "routing_reason": routing.reason},
        )
        provider = self._resolve_provider(routing)
        request = ModelGenerationRequest(
            model_id=routing.model_id,
            system_prompt=self._system_prompt(response_model),
            prompt=self._user_prompt(instruction, payload, response_model),
        )
        try:
            result = provider.generate(request)
        except Exception:
            self._emit(
                event_context,
                ExecutionEventType.MODEL_INVOKED,
                ExecutionEventStatus.FAILED,
                f"Model invocation failed for {routing.model_id}.",
                model_id=routing.model_id,
                model_provider_id=routing.provider_id,
            )
            raise
        self._emit(
            event_context,
            ExecutionEventType.MODEL_INVOKED,
            ExecutionEventStatus.COMPLETED,
            f"Model invocation completed for {routing.model_id}.",
            model_id=routing.model_id,
            model_provider_id=routing.provider_id,
        )
        return self._parse_structured_response(response_model, result.text)

    def _emit(
        self,
        context: ExecutionEventContext | None,
        event_type: ExecutionEventType,
        status: ExecutionEventStatus,
        summary: str,
        *,
        model_id: str | None = None,
        model_provider_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if self._event_publisher is None or context is None:
            return
        self._event_publisher.publish(
            ExecutionEvent(
                session_id=context.session_id,
                task_id=context.task_id,
                user_id=context.user_id,
                event_type=event_type,
                component="agent_runtime",
                status=status,
                summary=summary,
                model_id=model_id,
                model_provider_id=model_provider_id,
                metadata=metadata or {},
            )
        )

    def _resolve_provider(self, routing: RoutingDecision) -> ModelProvider:
        provider = self._providers.get(routing.provider_id)
        if provider is None:
            raise AgentProviderResolutionError(
                f"No ModelProvider instance is configured for provider '{routing.provider_id}'."
            )
        return provider

    @staticmethod
    def _system_prompt(response_model: type[BaseModel]) -> str:
        return (
            "You are the AEGIS prototype agent. "
            "Return JSON only, with no markdown and no chain-of-thought. "
            f"The response must validate against this schema: {json.dumps(response_model.model_json_schema(), sort_keys=True)}"
        )

    @staticmethod
    def _user_prompt(
        instruction: str,
        payload: BaseModel,
        response_model: type[BaseModel],
    ) -> str:
        return (
            f"{instruction}\n\n"
            "Request payload:\n"
            f"{payload.model_dump_json(indent=2)}\n\n"
            "Return a JSON object that matches this schema exactly:\n"
            f"{json.dumps(response_model.model_json_schema(), indent=2, sort_keys=True)}"
        )

    @staticmethod
    def _parse_structured_response(
        response_model: type[_MODEL_RESPONSE],
        raw_text: str,
    ) -> _MODEL_RESPONSE:
        candidates = [raw_text.strip()]
        extracted = RouterAgentRuntime._extract_json_text(raw_text)
        if extracted not in candidates:
            candidates.append(extracted)

        for candidate in candidates:
            try:
                return response_model.model_validate_json(candidate)
            except ValidationError:
                continue
            except ValueError:
                continue

        raise AgentModelResponseError(
            f"Model response did not match schema '{response_model.__name__}'."
        )

    @staticmethod
    def _extract_json_text(raw_text: str) -> str:
        stripped = raw_text.strip()
        decoder = json.JSONDecoder()
        for marker in ("{", "["):
            start_index = stripped.find(marker)
            if start_index < 0:
                continue
            try:
                _, end_index = decoder.raw_decode(stripped[start_index:])
            except json.JSONDecodeError:
                continue
            return stripped[start_index : start_index + end_index]
        return stripped

    @staticmethod
    def _infer_router_modality(attachments) -> str | None:
        if not attachments:
            return None
        media_types = {
            attachment.media_type
            for attachment in attachments
            if attachment.media_type is not None
        }
        if any(media_type.startswith("image/") for media_type in media_types):
            return "image"
        if any(
            "sheet" in media_type
            or "excel" in media_type
            or "csv" in media_type
            for media_type in media_types
        ):
            return "spreadsheet"
        return "scanned_document"

    def _validate_plan(self, plan: PlanProposal, request: PlanGenerationRequest) -> None:
        if len(plan.steps) > self._config.planning.max_plan_steps:
            raise AgentModelResponseError(
                "Model proposed more steps than the configured planning limit."
            )

        available_capabilities = set(request.available_capabilities)
        planned_capabilities = [step.capability_name for step in plan.steps]
        unavailable = [
            capability
            for capability in planned_capabilities
            if capability not in available_capabilities
        ]
        if unavailable:
            raise AgentModelResponseError(
                f"Model proposed unavailable capabilities: {', '.join(unavailable)}."
            )

        template, optional_steps = self._workflow_template(request)
        order = {capability: index for index, capability in enumerate(template)}
        try:
            indexes = [order[capability] for capability in planned_capabilities]
        except KeyError as error:
            raise AgentModelResponseError(
                f"Model proposed capability '{error.args[0]}' outside the supported workflow."
            ) from error

        if indexes != sorted(indexes):
            raise AgentModelResponseError(
                "Model proposed capabilities in an order that conflicts with the workflow."
            )

        required_steps = [
            capability
            for capability in template
            if capability not in optional_steps and capability in available_capabilities
        ]
        missing_steps = [
            capability for capability in required_steps if capability not in planned_capabilities
        ]
        if missing_steps:
            raise AgentModelResponseError(
                f"Model omitted required capabilities: {', '.join(missing_steps)}."
            )

        if "finish" in available_capabilities and planned_capabilities[-1] != "finish":
            raise AgentModelResponseError("If available, finish must be the final planned step.")

    @staticmethod
    def _workflow_template(
        request: PlanGenerationRequest,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        workflow = workflow_for_intent(request.intent)
        if workflow == workflow.COMPUTATION:
            return (
                (
                    "inspect_spreadsheet",
                    "generate_code",
                    "run_code",
                    "verify_result",
                    "generate_excel",
                    "finish",
                ),
                frozenset(),
            )
        if workflow == workflow.SCANNED_DOCUMENT_APPROVAL:
            return (
                (
                    "extract_document",
                    "ocr_document",
                    "search_knowledge",
                    "draft_approval_note",
                    "generate_word",
                    "verify_result",
                    "finish",
                ),
                frozenset({"search_knowledge"}),
            )
        return (
            (
                "analyze_image",
                "verify_result",
                "finish",
            ),
            frozenset(),
        )

    @staticmethod
    def _validate_observation_decision(
        decision: ObservationDecision,
        request: ObservationReasoningRequest,
    ) -> None:
        if decision.directive == AgentDirective.REQUEST_APPROVAL:
            return

        assert decision.proposed_action is not None
        allowed_actions = set(request.previous_context.allowed_next_actions)
        if allowed_actions and decision.proposed_action.action not in allowed_actions:
            raise AgentModelResponseError(
                f"Model proposed action '{decision.proposed_action.action}' outside allowed_next_actions."
            )
