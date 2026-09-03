"""Skeleton import checks for the Phase 0.1 package layout."""

import aegis
import aegis.agent
import aegis.audit
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
from aegis.router.provider import ModelGenerationRequest, ModelGenerationResult, ModelProvider
from aegis.broker import CapabilityBroker, RegistryCapabilityBroker
from aegis.capabilities import Capability, CapabilityRegistry
from aegis.orchestration import ExecutionController, WorkflowName


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


def test_orchestration_and_broker_interfaces_are_importable():
    assert CapabilityBroker.__name__ == "CapabilityBroker"
    assert RegistryCapabilityBroker.__name__ == "RegistryCapabilityBroker"
    assert Capability.__name__ == "Capability"
    assert CapabilityRegistry.__name__ == "CapabilityRegistry"
    assert ExecutionController.__name__ == "ExecutionController"
    assert WorkflowName.COMPUTATION == "computation"
