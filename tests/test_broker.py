"""Tests for registry-backed capability Broker resolution and invocation."""

from __future__ import annotations

from aegis.broker import RegistryCapabilityBroker
from aegis.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityMetadata,
    CapabilityRegistry,
)
from aegis.config import CapabilityConfig, CapabilityRegistryConfig, load_config
from aegis.orchestration import ExecutionController, WorkflowName
from aegis.schemas import (
    AgentDecision,
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
    TaskState,
)


class SuccessfulMockCapability(Capability):
    """Test-only capability that returns a successful result."""

    def __init__(self, name: str) -> None:
        self._metadata = _metadata(name)
        self.requests: list[CapabilityRequest] = []

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        self.requests.append(request)
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output={"mock": "success"},
        )


class FailingMockCapability(Capability):
    """Test-only capability that returns a controlled failure."""

    def __init__(self, name: str) -> None:
        self._metadata = _metadata(name)
        self.requests: list[CapabilityRequest] = []

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        self.requests.append(request)
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.FAILED,
            error="Synthetic capability failure.",
        )


class ObservationMockCapability(Capability):
    """Test-only capability that returns an observation for Controller recording."""

    def __init__(self, name: str) -> None:
        self._metadata = _metadata(name)
        self.requests: list[CapabilityRequest] = []

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        self.requests.append(request)
        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            observations=[
                Observation(
                    source=self.metadata.name,
                    kind="mock_result",
                    summary="Synthetic observation from a test-only capability.",
                    request_id=request.request_id,
                )
            ],
        )


def _metadata(name: str) -> CapabilityMetadata:
    return CapabilityMetadata(
        name=name,
        kind=CapabilityKind.TOOL,
        description="Test-only mock capability.",
    )


def _broker_with(capability: Capability) -> RegistryCapabilityBroker:
    registry = CapabilityRegistry(load_config().capabilities)
    registry.register(capability)
    return RegistryCapabilityBroker(registry)


def test_broker_resolves_and_invokes_only_registered_capabilities():
    capability = SuccessfulMockCapability("inspect_spreadsheet")
    broker = _broker_with(capability)
    request = CapabilityRequest(capability_name="inspect_spreadsheet")

    result = broker.invoke(request)

    assert result.status == CapabilityResultStatus.SUCCEEDED
    assert result.output == {"mock": "success"}
    assert capability.requests == [request]


def test_broker_returns_controlled_failure_for_unknown_or_unregistered_capability():
    broker = RegistryCapabilityBroker(CapabilityRegistry(load_config().capabilities))

    unknown = broker.invoke(CapabilityRequest(capability_name="unknown_capability"))
    unregistered = broker.invoke(CapabilityRequest(capability_name="inspect_spreadsheet"))

    assert unknown.status == CapabilityResultStatus.REJECTED
    assert unknown.error == "Unknown capability 'unknown_capability'."
    assert unregistered.status == CapabilityResultStatus.REJECTED
    assert unregistered.error == "Capability 'inspect_spreadsheet' is not registered."


def test_broker_returns_controlled_failure_for_disabled_capability():
    registry = CapabilityRegistry(
        CapabilityRegistryConfig(
            capabilities=[
                CapabilityConfig(
                    name="inspect_spreadsheet",
                    kind="tool",
                    enabled=False,
                    description="Disabled test capability.",
                    handler_key="test.inspect",
                )
            ]
        )
    )
    broker = RegistryCapabilityBroker(registry)

    result = broker.invoke(CapabilityRequest(capability_name="inspect_spreadsheet"))

    assert result.status == CapabilityResultStatus.REJECTED
    assert result.error == "Capability 'inspect_spreadsheet' is disabled."


def test_broker_preserves_controlled_capability_failure():
    capability = FailingMockCapability("inspect_spreadsheet")
    broker = _broker_with(capability)

    result = broker.invoke(CapabilityRequest(capability_name="inspect_spreadsheet"))

    assert result.status == CapabilityResultStatus.FAILED
    assert result.error == "Synthetic capability failure."
    assert len(capability.requests) == 1


def test_controller_records_observation_from_registry_backed_broker():
    capability = ObservationMockCapability("inspect_spreadsheet")
    broker = _broker_with(capability)
    state = TaskState(user_goal="Inspect an equipment spreadsheet.")
    controller = ExecutionController(state, WorkflowName.COMPUTATION, broker)

    controller.execute(AgentDecision(action="inspect_spreadsheet"))

    assert capability.requests[0].task_id == state.session_id
    assert state.current_step == "generate"
    assert [observation.kind for observation in state.observations] == [
        "mock_result",
        "capability_succeeded",
    ]
