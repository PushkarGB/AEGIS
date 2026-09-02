"""Validated configuration schemas for the AEGIS prototype."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
CAPABILITY_PATTERN = r"^[a-z][a-z0-9_]*$"


def _ensure_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class AgentPlanningConfig(BaseModel):
    """Agent planning limits kept outside business logic."""

    model_config = ConfigDict(extra="forbid")

    max_plan_steps: int = Field(default=8, ge=1, le=50)
    max_observation_chars: int = Field(default=12000, ge=256)


class AgentConfig(BaseModel):
    """General-purpose agent runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    default_model_role: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    planning: AgentPlanningConfig = Field(default_factory=AgentPlanningConfig)
    allowed_modalities: list[str] = Field(default_factory=list)
    default_capabilities: list[str] = Field(default_factory=list)

    @field_validator("allowed_modalities")
    @classmethod
    def validate_allowed_modalities(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "allowed_modalities")

    @field_validator("default_capabilities")
    @classmethod
    def validate_default_capabilities(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "default_capabilities")


class ModelProviderConfig(BaseModel):
    """Externalized provider metadata for later router/provider phases."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    kind: Literal["local", "api", "mock"]
    enabled: bool = True
    endpoint: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_endpoint_requirements(self) -> "ModelProviderConfig":
        if self.kind in {"local", "api"} and not self.endpoint:
            raise ValueError("local and api providers require an endpoint")
        if self.kind == "mock" and self.endpoint is not None:
            raise ValueError("mock providers must not define an endpoint")
        return self


class ModelConfig(BaseModel):
    """Per-model registry entry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    provider: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    roles: list[str] = Field(min_length=1)
    enabled: bool = True
    context_window: int | None = Field(default=None, ge=1)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "roles")


class ModelRegistryConfig(BaseModel):
    """Validated registry for providers, models, and role defaults."""

    model_config = ConfigDict(extra="forbid")

    providers: list[ModelProviderConfig] = Field(min_length=1)
    models: list[ModelConfig] = Field(min_length=1)
    role_defaults: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_registry(self) -> "ModelRegistryConfig":
        providers_by_id = {provider.id: provider for provider in self.providers}
        if len(providers_by_id) != len(self.providers):
            raise ValueError("provider ids must be unique")

        models_by_id = {model.id: model for model in self.models}
        if len(models_by_id) != len(self.models):
            raise ValueError("model ids must be unique")

        for model in self.models:
            provider = providers_by_id.get(model.provider)
            if provider is None:
                raise ValueError(
                    f"model '{model.id}' references unknown provider '{model.provider}'"
                )
            if model.enabled and not provider.enabled:
                raise ValueError(
                    f"enabled model '{model.id}' cannot use disabled provider '{provider.id}'"
                )

        for role, model_id in self.role_defaults.items():
            model = models_by_id.get(model_id)
            if model is None:
                raise ValueError(
                    f"role default '{role}' references unknown model '{model_id}'"
                )
            if role not in model.roles:
                raise ValueError(
                    f"role default '{role}' must reference a model that declares that role"
                )
            if not model.enabled:
                raise ValueError(
                    f"role default '{role}' cannot reference disabled model '{model_id}'"
                )

        return self


class CapabilityConfig(BaseModel):
    """Prototype capability registry entry."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(min_length=1, pattern=CAPABILITY_PATTERN)
    kind: Literal["tool", "model", "knowledge", "control"]
    enabled: bool = True
    description: str = Field(min_length=1)
    handler_key: str | None = None
    model_role: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    input_modalities: list[str] = Field(default_factory=list)

    @field_validator("input_modalities")
    @classmethod
    def validate_input_modalities(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "input_modalities")

    @model_validator(mode="after")
    def validate_kind_specific_fields(self) -> "CapabilityConfig":
        if self.kind == "model":
            if not self.model_role:
                raise ValueError("model capabilities require model_role")
            if self.handler_key is not None:
                raise ValueError("model capabilities must not define handler_key")
        else:
            if self.model_role is not None:
                raise ValueError("non-model capabilities must not define model_role")
            if not self.handler_key:
                raise ValueError("non-model capabilities require handler_key")
        return self


class CapabilityRegistryConfig(BaseModel):
    """Validated bounded capability registry."""

    model_config = ConfigDict(extra="forbid")

    capabilities: list[CapabilityConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "CapabilityRegistryConfig":
        capability_names = {capability.name for capability in self.capabilities}
        if len(capability_names) != len(self.capabilities):
            raise ValueError("capability names must be unique")
        return self


class RuntimeSandboxSettings(BaseModel):
    """Sandbox configuration with local-first safety defaults."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    network_enabled: bool = False
    container_runtime: str = Field(default="docker", min_length=1)

    @model_validator(mode="after")
    def validate_network_policy(self) -> "RuntimeSandboxSettings":
        if self.enabled and self.network_enabled:
            raise ValueError("sandboxed execution must keep networking disabled")
        return self


class RuntimeControllerLimits(BaseModel):
    """Bounded runtime limits for the future controller loop."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=2, ge=0, le=10)
    max_iterations: int = Field(default=6, ge=1, le=50)


class RuntimeUISettings(BaseModel):
    """UI execution-event settings that preserve architecture rules."""

    model_config = ConfigDict(extra="forbid")

    stream_execution_events: bool = True
    show_chain_of_thought: bool = False

    @model_validator(mode="after")
    def validate_ui_visibility(self) -> "RuntimeUISettings":
        if self.show_chain_of_thought:
            raise ValueError("UI configuration must not expose chain-of-thought")
        return self


class RuntimeSettings(BaseModel):
    """Environment and runtime configuration for the prototype shell."""

    model_config = ConfigDict(extra="forbid")

    environment: Literal["development", "testing", "production"] = "development"
    session_db_path: Path = Path("data/sessions.sqlite3")
    audit_log_path: Path = Path("data/audit/events.jsonl")
    artifacts_dir: Path = Path("artifacts")
    sandbox: RuntimeSandboxSettings = Field(default_factory=RuntimeSandboxSettings)
    controller: RuntimeControllerLimits = Field(default_factory=RuntimeControllerLimits)
    ui: RuntimeUISettings = Field(default_factory=RuntimeUISettings)
    allow_temporary_api_provider: bool = False

    @model_validator(mode="after")
    def validate_provider_policy(self) -> "RuntimeSettings":
        if self.environment == "production" and self.allow_temporary_api_provider:
            raise ValueError(
                "temporary api provider access is development/testing only"
            )
        return self


class AegisConfig(BaseModel):
    """Combined validated application configuration."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentConfig
    models: ModelRegistryConfig
    capabilities: CapabilityRegistryConfig
    runtime: RuntimeSettings
