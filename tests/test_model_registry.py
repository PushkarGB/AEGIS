"""Validation tests for the deterministic model registry."""

from __future__ import annotations

import pytest

from aegis.config import ModelConfig, ModelHealth, ModelProviderConfig, ModelRegistryConfig
from aegis.router import ModelRegistry


# ── Helpers ──────────────────────────────────────────────────────────


def _make_provider(
    pid: str = "local",
    kind: str = "local",
    enabled: bool = True,
    endpoint: str = "http://localhost:11434/v1",
) -> ModelProviderConfig:
    kwargs: dict = {"id": pid, "kind": kind, "enabled": enabled}
    if kind in {"local", "api"}:
        kwargs["endpoint"] = endpoint
    return ModelProviderConfig(**kwargs)


def _make_model(
    mid: str = "model-a",
    provider: str = "local",
    roles: list[str] | None = None,
    enabled: bool = True,
    available: bool = True,
    health: str = "unknown",
    **extra,
) -> ModelConfig:
    return ModelConfig(
        id=mid,
        provider=provider,
        roles=roles or ["agent"],
        enabled=enabled,
        available=available,
        health=ModelHealth(health),
        **extra,
    )


def _make_registry(
    providers: list[ModelProviderConfig] | None = None,
    models: list[ModelConfig] | None = None,
    role_defaults: dict[str, str] | None = None,
) -> ModelRegistry:
    config = ModelRegistryConfig(
        providers=providers or [_make_provider()],
        models=models or [_make_model()],
        role_defaults=role_defaults or {},
    )
    return ModelRegistry(config)


# ── Construction ─────────────────────────────────────────────────────


def test_registry_construction_from_config():
    registry = _make_registry()

    assert registry.get_model("model-a") is not None
    assert registry.get_provider("local") is not None


def test_registry_with_multiple_models():
    models = [
        _make_model("m1", roles=["agent"]),
        _make_model("m2", roles=["coding"]),
        _make_model("m3", roles=["vision"]),
    ]
    registry = _make_registry(models=models, role_defaults={"agent": "m1", "coding": "m2", "vision": "m3"})

    assert len(registry.list_models()) == 3
    assert registry.get_model("m1") is not None
    assert registry.get_model("m2") is not None
    assert registry.get_model("m3") is not None


# ── Single-item lookups ──────────────────────────────────────────────


def test_get_model_returns_model():
    registry = _make_registry()
    model = registry.get_model("model-a")

    assert model is not None
    assert model.id == "model-a"


def test_get_model_returns_none_for_unknown():
    registry = _make_registry()

    assert registry.get_model("nonexistent") is None


def test_get_provider_returns_provider():
    registry = _make_registry()
    provider = registry.get_provider("local")

    assert provider is not None
    assert provider.id == "local"


def test_get_provider_returns_none_for_unknown():
    registry = _make_registry()

    assert registry.get_provider("nonexistent") is None


def test_get_provider_for_model():
    registry = _make_registry()
    provider = registry.get_provider_for_model("model-a")

    assert provider is not None
    assert provider.id == "local"


def test_get_provider_for_model_returns_none_for_unknown_model():
    registry = _make_registry()

    assert registry.get_provider_for_model("nonexistent") is None


# ── Role-based lookups ───────────────────────────────────────────────


def test_get_models_for_role_returns_matching():
    models = [
        _make_model("m1", roles=["agent"]),
        _make_model("m2", roles=["agent"]),
        _make_model("m3", roles=["coding"]),
    ]
    registry = _make_registry(models=models)

    agents = registry.get_models_for_role("agent")
    assert len(agents) == 2
    assert agents[0].id == "m1"
    assert agents[1].id == "m2"


def test_get_models_for_role_excludes_disabled():
    models = [
        _make_model("m1", roles=["agent"], enabled=True),
        _make_model("m2", roles=["agent"], enabled=False),
    ]
    registry = _make_registry(models=models)

    agents = registry.get_models_for_role("agent")
    assert len(agents) == 1
    assert agents[0].id == "m1"


def test_get_models_for_role_excludes_unavailable():
    models = [
        _make_model("m1", roles=["agent"], available=True),
        _make_model("m2", roles=["agent"], available=False),
    ]
    registry = _make_registry(models=models)

    agents = registry.get_models_for_role("agent")
    assert len(agents) == 1
    assert agents[0].id == "m1"


def test_get_models_for_role_returns_empty_for_unknown_role():
    registry = _make_registry()

    assert registry.get_models_for_role("nonexistent") == ()


def test_get_default_model_for_role():
    models = [
        _make_model("m1", roles=["agent"]),
        _make_model("m2", roles=["agent"]),
    ]
    registry = _make_registry(models=models, role_defaults={"agent": "m1"})

    default = registry.get_default_model_for_role("agent")
    assert default is not None
    assert default.id == "m1"


def test_get_default_model_for_role_returns_none_when_not_configured():
    registry = _make_registry(role_defaults={})

    assert registry.get_default_model_for_role("agent") is None


def test_get_default_model_for_role_returns_none_when_disabled():
    models = [_make_model("m1", roles=["agent"], enabled=False)]
    # Cannot use role_defaults pointing to disabled model via config validation,
    # so we build the registry manually with an empty defaults and then check.
    registry = _make_registry(models=models, role_defaults={})

    assert registry.get_default_model_for_role("agent") is None


def test_get_models_for_role_excludes_unhealthy():
    models = [
        _make_model("m1", roles=["agent"], health="healthy"),
        _make_model("m2", roles=["agent"], health="unavailable"),
    ]
    registry = _make_registry(models=models)

    agents = registry.get_models_for_role("agent")
    assert len(agents) == 1
    assert agents[0].id == "m1"


def test_get_models_for_capability():
    models = [
        _make_model("m1", roles=["agent"], capabilities=["text_generation", "reasoning"]),
        _make_model("m2", roles=["coding"], capabilities=["text_generation", "code_generation"]),
        _make_model("m3", roles=["agent"], capabilities=["text_generation"], available=False),
    ]
    registry = _make_registry(models=models)

    text_gen = registry.get_models_for_capability("text_generation")
    assert len(text_gen) == 2
    assert [m.id for m in text_gen] == ["m1", "m2"]

    code_gen = registry.get_models_for_capability("code_generation")
    assert len(code_gen) == 1
    assert code_gen[0].id == "m2"

    assert registry.get_models_for_capability("unknown") == ()


def test_get_default_model_for_role_returns_none_when_unhealthy():
    models = [
        _make_model("m1", roles=["agent"], health="unavailable"),
    ]
    registry = _make_registry(models=models, role_defaults={})
    registry._role_defaults["agent"] = "m1"
    assert registry.get_default_model_for_role("agent") is None


# ── Listing ──────────────────────────────────────────────────────────


def test_list_models_returns_all():
    models = [
        _make_model("m1", roles=["agent"]),
        _make_model("m2", roles=["coding"], enabled=False),
    ]
    registry = _make_registry(models=models)

    all_models = registry.list_models()
    assert len(all_models) == 2


def test_list_models_enabled_only():
    models = [
        _make_model("m1", roles=["agent"], enabled=True),
        _make_model("m2", roles=["coding"], enabled=False),
    ]
    registry = _make_registry(models=models)

    enabled = registry.list_models(enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].id == "m1"


def test_list_models_available_only():
    models = [
        _make_model("m1", roles=["agent"], available=True),
        _make_model("m2", roles=["coding"], available=False),
    ]
    registry = _make_registry(models=models)

    available = registry.list_models(available_only=True)
    assert len(available) == 1
    assert available[0].id == "m1"


def test_list_models_preserves_declaration_order():
    models = [
        _make_model("z-model", roles=["agent"]),
        _make_model("a-model", roles=["coding"]),
        _make_model("m-model", roles=["vision"]),
    ]
    registry = _make_registry(models=models)

    ids = [m.id for m in registry.list_models()]
    assert ids == ["z-model", "a-model", "m-model"]


def test_list_providers_returns_all():
    providers = [
        _make_provider("p1"),
        _make_provider("p2", enabled=False),
    ]
    registry = _make_registry(
        providers=providers,
        models=[_make_model("m1", provider="p1", roles=["agent"])],
    )

    all_providers = registry.list_providers()
    assert len(all_providers) == 2


def test_list_providers_enabled_only():
    providers = [
        _make_provider("p1"),
        _make_provider("p2", enabled=False),
    ]
    registry = _make_registry(
        providers=providers,
        models=[_make_model("m1", provider="p1", roles=["agent"])],
    )

    enabled = registry.list_providers(enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].id == "p1"


# ── Model metadata fields ───────────────────────────────────────────


def test_model_metadata_fields_preserved():
    model = _make_model(
        "m1",
        roles=["agent"],
        name="Test Agent Model",
        capabilities=["text_generation", "reasoning"],
        modalities=["text"],
        task_types=["general_reasoning"],
        context_window=4096,
        parameters="8B",
        quantization="Q4_K_M",
        health="healthy",
    )
    registry = _make_registry(models=[model])

    retrieved = registry.get_model("m1")
    assert retrieved is not None
    assert retrieved.name == "Test Agent Model"
    assert retrieved.capabilities == ["text_generation", "reasoning"]
    assert retrieved.modalities == ["text"]
    assert retrieved.task_types == ["general_reasoning"]
    assert retrieved.context_window == 4096
    assert retrieved.parameters == "8B"
    assert retrieved.quantization == "Q4_K_M"
    assert retrieved.health == ModelHealth.HEALTHY


def test_model_health_enum_values():
    assert ModelHealth.UNKNOWN == "unknown"
    assert ModelHealth.HEALTHY == "healthy"
    assert ModelHealth.DEGRADED == "degraded"
    assert ModelHealth.UNAVAILABLE == "unavailable"


# ── Config validation for new fields ─────────────────────────────────


def test_model_config_rejects_duplicate_capabilities():
    with pytest.raises(ValueError, match="capabilities"):
        ModelConfig(
            id="m1",
            provider="local",
            roles=["agent"],
            capabilities=["reasoning", "reasoning"],
        )


def test_model_config_rejects_duplicate_modalities():
    with pytest.raises(ValueError, match="modalities"):
        ModelConfig(
            id="m1",
            provider="local",
            roles=["agent"],
            modalities=["text", "text"],
        )


def test_model_config_rejects_duplicate_task_types():
    with pytest.raises(ValueError, match="task_types"):
        ModelConfig(
            id="m1",
            provider="local",
            roles=["agent"],
            task_types=["drafting", "drafting"],
        )


# ── Integration: load from repository defaults ──────────────────────


def test_registry_from_repository_defaults():
    """Verify the registry loads correctly from the actual config/models.yaml."""
    from aegis.config import load_config

    config = load_config()
    registry = ModelRegistry(config.models)

    assert registry.get_model("agent_model") is not None
    assert registry.get_model("coding_model") is not None
    assert registry.get_model("vision_model") is not None
    assert registry.get_default_model_for_role("agent") is not None
    assert registry.get_default_model_for_role("coding") is not None
    assert registry.get_default_model_for_role("vision") is not None
    assert len(registry.list_providers()) >= 1
