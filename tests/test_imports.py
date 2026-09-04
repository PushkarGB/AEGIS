"""Skeleton import checks for the Phase 0.1 package layout."""

import aegis
import aegis.agent
import aegis.audit
import aegis.agent
import aegis.broker
import aegis.capabilities
import aegis.config
import aegis.data
import aegis.orchestration
import aegis.router
import aegis.security
import aegis.sessions
import aegis.skills
import aegis.schemas
from aegis.agent import (
    AgentDirective,
    AgentIntent,
    AgentRuntime,
    InputModality,
    IntentAnalysisRequest,
    IntentAnalysisResult,
    ObservationDecision,
    PlanGenerationRequest,
    PlanProposal,
    RouterAgentRuntime,
    SandboxObservationLoop,
    SandboxRecoveryResult,
)
from aegis.router.provider import ModelGenerationRequest, ModelGenerationResult, ModelProvider
from aegis.router import (
    APIModelProvider,
    FallbackInfo,
    LocalModelProvider,
    MockModelProvider,
    ModelProviderConfigurationError,
    ModelProviderConnectionError,
    ModelRegistry,
    ModelRouter,
    OllamaHealthStatus,
    OllamaModelProvider,
    RoutingDecision,
    RoutingError,
    TemporaryAPIProviderDisabledError,
)
from aegis.broker import CapabilityBroker, RegistryCapabilityBroker
from aegis.capabilities import (
    Capability,
    CapabilityRegistry,
    DockerSandboxRunner,
    GenerateCodeCapability,
    GenerateExcelCapability,
    generate_code,
    generate_excel_deliverable,
    InspectSpreadsheetCapability,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
    SandboxRunner,
    VerificationCheck,
    VerificationOutcome,
    VerifyResultCapability,
    WorkbookInspection,
    inspect_spreadsheet,
    run_code,
    verify_computation_result,
)
from aegis.orchestration import ExecutionController, WorkflowName
from aegis.skills import (
    CodeGenerationPrompt,
    ComputationContext,
    ExecutionOutcome,
    build_code_generation_prompt,
    build_retry_context,
    parse_execution_observation,
    prepare_generate_code_inputs,
    prepare_run_code_inputs,
)


def test_package_version():
    assert aegis.__version__ == "0.0.1"


def test_subpackages_importable():
    assert aegis.agent.__doc__
    assert aegis.orchestration.__doc__
    assert aegis.broker.__doc__
    assert aegis.router.__doc__
    assert aegis.capabilities.__doc__
    assert aegis.config.__doc__
    assert aegis.skills.__doc__
    assert aegis.sessions.__doc__
    assert aegis.audit.__doc__
    assert aegis.security.__doc__
    assert aegis.data.__doc__
    assert aegis.schemas.__doc__


def test_model_provider_interface_is_importable():
    assert issubclass(ModelProvider, object)
    assert ModelProvider.__name__ == "ModelProvider"
    assert ModelGenerationRequest.__name__ == "ModelGenerationRequest"
    assert ModelGenerationResult.__name__ == "ModelGenerationResult"
    assert MockModelProvider.__name__ == "MockModelProvider"
    assert LocalModelProvider.__name__ == "LocalModelProvider"
    assert APIModelProvider.__name__ == "APIModelProvider"
    assert OllamaModelProvider.__name__ == "OllamaModelProvider"
    assert issubclass(OllamaModelProvider, ModelProvider)
    assert OllamaHealthStatus.__name__ == "OllamaHealthStatus"
    assert issubclass(ModelProviderConfigurationError, ValueError)
    assert issubclass(ModelProviderConnectionError, RuntimeError)
    assert issubclass(TemporaryAPIProviderDisabledError, ValueError)


def test_orchestration_and_broker_interfaces_are_importable():
    assert CapabilityBroker.__name__ == "CapabilityBroker"
    assert RegistryCapabilityBroker.__name__ == "RegistryCapabilityBroker"
    assert Capability.__name__ == "Capability"
    assert CapabilityRegistry.__name__ == "CapabilityRegistry"
    assert InspectSpreadsheetCapability.__name__ == "InspectSpreadsheetCapability"
    assert WorkbookInspection.__name__ == "WorkbookInspection"
    assert callable(inspect_spreadsheet)
    assert ExecutionController.__name__ == "ExecutionController"
    assert WorkflowName.COMPUTATION == "computation"


def test_agent_runtime_interfaces_are_importable():
    assert AgentRuntime.__name__ == "AgentRuntime"
    assert RouterAgentRuntime.__name__ == "RouterAgentRuntime"
    assert AgentIntent.COMPUTATION == "computation"
    assert InputModality.SPREADSHEET == "spreadsheet"
    assert AgentDirective.CONTINUE == "continue"
    assert IntentAnalysisRequest.__name__ == "IntentAnalysisRequest"
    assert IntentAnalysisResult.__name__ == "IntentAnalysisResult"
    assert PlanGenerationRequest.__name__ == "PlanGenerationRequest"
    assert PlanProposal.__name__ == "PlanProposal"
    assert ObservationDecision.__name__ == "ObservationDecision"
    assert SandboxObservationLoop.__name__ == "SandboxObservationLoop"
    assert SandboxRecoveryResult.__name__ == "SandboxRecoveryResult"


def test_model_registry_and_router_are_importable():
    assert ModelRegistry.__name__ == "ModelRegistry"
    assert ModelRouter.__name__ == "ModelRouter"
    assert RoutingDecision.__name__ == "RoutingDecision"
    assert FallbackInfo.__name__ == "FallbackInfo"
    assert issubclass(RoutingError, ValueError)


def test_computation_skill_is_importable():
    assert ComputationContext.__name__ == "ComputationContext"
    assert CodeGenerationPrompt.__name__ == "CodeGenerationPrompt"
    assert ExecutionOutcome.__name__ == "ExecutionOutcome"
    assert callable(build_code_generation_prompt)
    assert callable(prepare_generate_code_inputs)
    assert callable(prepare_run_code_inputs)
    assert callable(parse_execution_observation)
    assert callable(build_retry_context)


def test_generate_and_run_code_capabilities_are_importable():
    assert GenerateCodeCapability.__name__ == "GenerateCodeCapability"
    assert callable(generate_code)
    assert RunCodeCapability.__name__ == "RunCodeCapability"
    assert issubclass(SandboxRunner, object)
    assert MockSandboxRunner.__name__ == "MockSandboxRunner"
    assert issubclass(DockerSandboxRunner, SandboxRunner)
    assert callable(run_code)
    assert SandboxResult.__name__ == "SandboxResult"


def test_verify_result_capability_is_importable():
    assert VerifyResultCapability.__name__ == "VerifyResultCapability"
    assert callable(verify_computation_result)
    assert VerificationOutcome.__name__ == "VerificationOutcome"
    assert VerificationCheck.__name__ == "VerificationCheck"


def test_generate_excel_capability_is_importable():
    assert GenerateExcelCapability.__name__ == "GenerateExcelCapability"
    assert callable(generate_excel_deliverable)


def test_auth_package_is_importable():
    import aegis.auth
    from aegis.auth import (
        AuditGuard,
        AuthService,
        AuthenticationError,
        AuthorizationError,
        Permission,
        PrototypeCredentialStore,
        SessionGuard,
        SystemGuard,
        TokenStore,
        UserIdentity,
        UserRole,
        has_permission,
        require_permission,
    )
    assert UserRole.USER == "user"
    assert UserRole.ADMIN == "admin"
    assert AuthService.__name__ == "AuthService"
    assert SessionGuard.__name__ == "SessionGuard"
    assert AuditGuard.__name__ == "AuditGuard"
    assert SystemGuard.__name__ == "SystemGuard"
    assert issubclass(AuthenticationError, Exception)
    assert issubclass(AuthorizationError, Exception)
    assert callable(has_permission)
    assert callable(require_permission)


def test_authorized_session_service_is_importable():
    from aegis.sessions import AuthorizedSessionService
    assert AuthorizedSessionService.__name__ == "AuthorizedSessionService"


def test_auth_config_is_importable():
    from aegis.config import AuthConfig, AegisConfig
    assert AuthConfig.__name__ == "AuthConfig"
    assert "auth" in AegisConfig.model_fields


def test_security_network_monitoring_is_importable():
    from aegis.security import (
        AuthorizedNetworkMonitor,
        InMemoryNetworkCollector,
        LocalConnectionCollector,
        NetworkClassification,
        NetworkCollector,
        NetworkMonitor,
        NetworkObservation,
        NetworkPolicy,
        NetworkSummary,
        PolicyViolation,
        StandardNetworkMonitor,
        TrafficDirection,
        TrafficStatus,
        classify_destination,
        determine_traffic_direction,
        is_internal_ip,
    )

    assert issubclass(NetworkMonitor, object)
    assert StandardNetworkMonitor.__name__ == "StandardNetworkMonitor"
    assert AuthorizedNetworkMonitor.__name__ == "AuthorizedNetworkMonitor"
    assert issubclass(NetworkCollector, object)
    assert InMemoryNetworkCollector.__name__ == "InMemoryNetworkCollector"
    assert LocalConnectionCollector.__name__ == "LocalConnectionCollector"
    assert NetworkClassification.INTERNAL == "INTERNAL"
    assert NetworkClassification.EXTERNAL == "EXTERNAL"
    assert NetworkClassification.BLOCKED == "BLOCKED"
    assert NetworkClassification.UNKNOWN == "UNKNOWN"
    assert TrafficDirection.LOOPBACK == "LOOPBACK"
    assert TrafficStatus.OBSERVED == "OBSERVED"
    assert NetworkObservation.__name__ == "NetworkObservation"
    assert NetworkPolicy.__name__ == "NetworkPolicy"
    assert callable(classify_destination)
    assert callable(determine_traffic_direction)
    assert callable(is_internal_ip)


def test_audit_service_is_importable():
    from aegis.audit import AuditService, AuthorizedAuditService
    assert AuditService.__name__ == "AuditService"
    assert AuthorizedAuditService.__name__ == "AuthorizedAuditService"


def test_ui_package_is_importable():
    from aegis.ui import UIBackendService, create_app, RuntimeTaskRunner, DeterministicTaskRunner
    assert UIBackendService.__name__ == "UIBackendService"
    assert RuntimeTaskRunner.__name__ == "RuntimeTaskRunner"
    assert DeterministicTaskRunner.__name__ == "DeterministicTaskRunner"
    assert callable(create_app)


def test_real_computation_workflow_classes_are_importable():
    from aegis.capabilities import FinishCapability
    from aegis.orchestration import RuntimeTaskRunner
    assert FinishCapability.__name__ == "FinishCapability"
    assert RuntimeTaskRunner.__name__ == "RuntimeTaskRunner"


def test_document_drafting_classes_are_importable():
    from aegis.capabilities import (
        DraftApprovalNoteCapability,
        ExtractDocumentCapability,
        GenerateWordCapability,
        create_approval_note_docx,
        extract_document_text,
        verify_document_drafting_result,
    )
    from aegis.data import DocumentCategory, DocumentTypeResult, identify_document_type
    assert ExtractDocumentCapability.__name__ == "ExtractDocumentCapability"
    assert DraftApprovalNoteCapability.__name__ == "DraftApprovalNoteCapability"
    assert GenerateWordCapability.__name__ == "GenerateWordCapability"
    assert callable(create_approval_note_docx)
    assert callable(extract_document_text)
    assert callable(verify_document_drafting_result)
    assert DocumentCategory.PDF == "pdf"
    assert DocumentTypeResult.__name__ == "DocumentTypeResult"
    assert callable(identify_document_type)


def test_artifacts_package_is_importable():
    from aegis.artifacts import ArtifactRecord, ArtifactStore, infer_artifact_media_type
    assert ArtifactRecord.__name__ == "ArtifactRecord"
    assert ArtifactStore.__name__ == "ArtifactStore"
    assert callable(infer_artifact_media_type)


