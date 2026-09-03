"""Deterministic model registry backed by externalized configuration.

The registry provides read-only, deterministic access to model definitions
and provider metadata loaded from external configuration. It does not manage
connectivity or runtime state.
"""

from __future__ import annotations

from aegis.config import ModelConfig, ModelHealth, ModelProviderConfig, ModelRegistryConfig


class ModelRegistry:
    """Read-only registry of configured models and providers.

    Built from a validated ``ModelRegistryConfig``, the registry provides
    deterministic lookups by model ID, provider ID, and model role. All
    listing methods preserve the original configuration declaration order.
    """

    def __init__(self, config: ModelRegistryConfig) -> None:
        self._config = config
        self._models_by_id: dict[str, ModelConfig] = {
            model.id: model for model in config.models
        }
        self._providers_by_id: dict[str, ModelProviderConfig] = {
            provider.id: provider for provider in config.providers
        }
        self._role_defaults: dict[str, str] = dict(config.role_defaults)
        # Preserve declaration order for deterministic listing
        self._model_order: tuple[str, ...] = tuple(m.id for m in config.models)
        self._provider_order: tuple[str, ...] = tuple(p.id for p in config.providers)

    @staticmethod
    def _is_available(model: ModelConfig) -> bool:
        return (
            model.enabled
            and model.available
            and model.health != ModelHealth.UNAVAILABLE
        )

    # ── Single-item lookups ──────────────────────────────────────────

    def get_model(self, model_id: str) -> ModelConfig | None:
        """Return the model definition for *model_id*, or ``None``."""
        return self._models_by_id.get(model_id)

    def get_provider(self, provider_id: str) -> ModelProviderConfig | None:
        """Return the provider definition for *provider_id*, or ``None``."""
        return self._providers_by_id.get(provider_id)

    def get_provider_for_model(self, model_id: str) -> ModelProviderConfig | None:
        """Return the provider associated with the given model, or ``None``."""
        model = self.get_model(model_id)
        if model is None:
            return None
        return self._providers_by_id.get(model.provider)

    # ── Role-based lookups ───────────────────────────────────────────

    def get_models_for_role(self, role: str) -> tuple[ModelConfig, ...]:
        """Return all enabled and available models declaring *role*, in declaration order."""
        return tuple(
            self._models_by_id[mid]
            for mid in self._model_order
            if mid in self._models_by_id
            and role in self._models_by_id[mid].roles
            and self._is_available(self._models_by_id[mid])
        )

    def get_models_for_capability(self, capability: str) -> tuple[ModelConfig, ...]:
        """Return all enabled and available models declaring *capability*, in declaration order."""
        return tuple(
            self._models_by_id[mid]
            for mid in self._model_order
            if mid in self._models_by_id
            and capability in self._models_by_id[mid].capabilities
            and self._is_available(self._models_by_id[mid])
        )

    def get_default_model_for_role(self, role: str) -> ModelConfig | None:
        """Return the configured default model for *role*, or ``None``.

        Returns ``None`` when no default is configured, the default model
        does not exist, or the default model is disabled or unavailable.
        """
        model_id = self._role_defaults.get(role)
        if model_id is None:
            return None
        model = self._models_by_id.get(model_id)
        if model is None or not self._is_available(model):
            return None
        return model

    # ── Listing ──────────────────────────────────────────────────────

    def list_models(
        self,
        *,
        enabled_only: bool = False,
        available_only: bool = False,
    ) -> tuple[ModelConfig, ...]:
        """List model definitions in declaration order with optional filters."""
        result: list[ModelConfig] = []
        for mid in self._model_order:
            model = self._models_by_id[mid]
            if enabled_only and not model.enabled:
                continue
            if available_only and not self._is_available(model):
                continue
            result.append(model)
        return tuple(result)

    def list_providers(self, *, enabled_only: bool = False) -> tuple[ModelProviderConfig, ...]:
        """List provider definitions in declaration order with optional filter."""
        providers = tuple(self._providers_by_id[pid] for pid in self._provider_order)
        if enabled_only:
            return tuple(p for p in providers if p.enabled)
        return providers
