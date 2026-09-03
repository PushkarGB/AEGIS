"""Deterministic Model Router and provider-neutral model-access boundary."""

from .provider import ModelGenerationRequest, ModelGenerationResult, ModelProvider
from .providers import (
    APIModelProvider,
    LocalModelProvider,
    MockModelProvider,
    ModelProviderConfigurationError,
    ModelProviderConnectionError,
    ModelProviderError,
    ModelProviderResponseError,
    TemporaryAPIProviderDisabledError,
)
from .registry import ModelRegistry
from .router import FallbackInfo, ModelRouter, RoutingDecision, RoutingError

__all__ = [
    "APIModelProvider",
    "FallbackInfo",
    "LocalModelProvider",
    "ModelGenerationRequest",
    "ModelGenerationResult",
    "ModelProvider",
    "ModelProviderConfigurationError",
    "ModelProviderConnectionError",
    "ModelProviderError",
    "ModelProviderResponseError",
    "ModelRegistry",
    "ModelRouter",
    "MockModelProvider",
    "RoutingDecision",
    "RoutingError",
    "TemporaryAPIProviderDisabledError",
]
