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
    generate_code,
    InspectSpreadsheetCapability,
    MockSandboxRunner,
    RunCodeCapability,
    SandboxResult,
    SandboxRunner,
    WorkbookInspection,
    inspect_spreadsheet,
    run_code,
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
