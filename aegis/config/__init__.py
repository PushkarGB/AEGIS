"""Configuration schemas and loaders for the AEGIS prototype."""

from .loader import DEFAULT_CONFIG_DIR, AegisConfigPaths, load_config, resolve_config_paths
from .schemas import (
    AegisConfig,
    AgentConfig,
    AgentPlanningConfig,
    AuthConfig,
    CapabilityConfig,
    CapabilityRegistryConfig,
    ModelConfig,
    ModelHealth,
    ModelProviderConfig,
    ModelRegistryConfig,
    OllamaConfig,
    RuntimeControllerLimits,
    RuntimeSandboxSettings,
    RuntimeSettings,
    RuntimeUISettings,
    load_ollama_config,
)

__all__ = [
    "AegisConfig",
    "AegisConfigPaths",
    "AgentConfig",
    "AgentPlanningConfig",
    "AuthConfig",
    "CapabilityConfig",
    "CapabilityRegistryConfig",
    "DEFAULT_CONFIG_DIR",
    "ModelConfig",
    "ModelHealth",
    "ModelProviderConfig",
    "ModelRegistryConfig",
    "OllamaConfig",
    "RuntimeControllerLimits",
    "RuntimeSandboxSettings",
    "RuntimeSettings",
    "RuntimeUISettings",
    "load_config",
    "load_ollama_config",
    "resolve_config_paths",
]
