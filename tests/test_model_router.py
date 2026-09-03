"""Validation tests for the deterministic model router."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.config import ModelConfig, ModelHealth, ModelProviderConfig, ModelRegistryConfig
from aegis.router import FallbackInfo, ModelRegistry, ModelRouter, RoutingDecision, RoutingError


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
    mid: str,
    provider: str = "local",
    roles: list[str] | None = None,
    enabled: bool = True,
    available: bool = True,
    **extra,
) -> ModelConfig:
    return ModelConfig(
        id=mid,
        provider=provider,
        roles=roles or ["agent"],
        enabled=enabled,
        available=available,
        **extra,
    )


def _standard_registry() -> ModelRegistry:
    """Three-model registry matching the architecture's deterministic routing rules."""
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("agent-model", roles=["agent"]),
            _make_model("coding-model", roles=["coding"]),
            _make_model("vision-model", roles=["vision"]),
        ],
        role_defaults={
            "agent": "agent-model",
            "coding": "coding-model",
            "vision": "vision-model",
        },
    )
    return ModelRegistry(config)


def _standard_router() -> ModelRouter:
    return ModelRouter(_standard_registry())


# ── Deterministic task-type → role routing ───────────────────────────


def test_route_general_reasoning_to_agent():
    decision = _standard_router().route("general_reasoning")

    assert decision.model_id == "agent-model"
    assert decision.role == "agent"
    assert decision.provider_id == "local"
    assert "general_reasoning" in decision.reason


def test_route_drafting_to_agent():
    decision = _standard_router().route("drafting")

    assert decision.model_id == "agent-model"
    assert decision.role == "agent"


def test_route_code_generation_to_coding():
    decision = _standard_router().route("code_generation")

    assert decision.model_id == "coding-model"
    assert decision.role == "coding"
    assert decision.provider_id == "local"
    assert "code_generation" in decision.reason


def test_route_visual_reasoning_to_vision():
    decision = _standard_router().route("visual_reasoning")

    assert decision.model_id == "vision-model"
    assert decision.role == "vision"


def test_route_image_analysis_to_vision():
    decision = _standard_router().route("image_analysis")

    assert decision.model_id == "vision-model"
    assert decision.role == "vision"


# ── RoutingDecision structure ────────────────────────────────────────


def test_routing_decision_contains_required_fields():
    decision = _standard_router().route("code_generation")

    assert isinstance(decision, RoutingDecision)
    assert isinstance(decision.model_id, str)
    assert isinstance(decision.provider_id, str)
    assert isinstance(decision.role, str)
    assert isinstance(decision.reason, str)
    assert len(decision.reason) > 0


def test_routing_decision_is_immutable():
    decision = _standard_router().route("code_generation")

    with pytest.raises(AttributeError):
        decision.model_id = "other"  # type: ignore[misc]


# ── Reason is human-readable and explainable ─────────────────────────


def test_routing_reason_is_explainable():
    decision = _standard_router().route("code_generation")

    # Must mention the task type and the model
    assert "code_generation" in decision.reason
    assert "coding-model" in decision.reason
    assert "local" in decision.reason


def test_routing_reason_includes_modality_when_provided():
    decision = _standard_router().route("visual_reasoning", modality="image")

    assert "image" in decision.reason


# ── Explicit role override ───────────────────────────────────────────


def test_explicit_role_override_bypasses_task_type_mapping():
    decision = _standard_router().route("code_generation", role="agent")

    assert decision.model_id == "agent-model"
    assert decision.role == "agent"
    assert "explicit role override" in decision.reason


def test_explicit_role_for_unknown_task_type():
    """An unknown task type is acceptable if an explicit role is provided."""
    decision = _standard_router().route("unknown_task", role="coding")

    assert decision.model_id == "coding-model"
    assert decision.role == "coding"


# ── Unknown task type without role ───────────────────────────────────


def test_unknown_task_type_without_role_raises():
    with pytest.raises(RoutingError, match="No routing rule"):
        _standard_router().route("unknown_task_type")


# ── Fallback behaviour ──────────────────────────────────────────────


def test_fallback_info_present_when_alternatives_exist():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("primary-agent", roles=["agent"]),
            _make_model("backup-agent", roles=["agent"]),
        ],
        role_defaults={"agent": "primary-agent"},
    )
    router = ModelRouter(ModelRegistry(config))
    decision = router.route("general_reasoning")

    assert decision.model_id == "primary-agent"
    assert decision.fallback is not None
    assert isinstance(decision.fallback, FallbackInfo)
    assert decision.fallback.model_id == "backup-agent"
    assert decision.fallback.provider_id == "local"
    assert "agent" in decision.fallback.reason


def test_no_fallback_when_single_model():
    decision = _standard_router().route("code_generation")

    assert decision.fallback is None


def test_unavailable_default_falls_back_to_alternative():
    """When the configured default is unavailable, the router uses another model."""
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("primary-agent", roles=["agent"], available=True),
            _make_model("backup-agent", roles=["agent"], available=True),
        ],
        role_defaults={"agent": "primary-agent"},
    )
    registry = ModelRegistry(config)
    # Simulate unavailability by patching the internal defaults
    registry._role_defaults["agent"] = "unavailable-model"

    router = ModelRouter(registry)
    decision = router.route("general_reasoning")

    # Should fall back to the first available candidate
    assert decision.model_id == "primary-agent"
    assert "fallback" in decision.reason


# ── No available models ──────────────────────────────────────────────


def test_no_available_models_raises_routing_error():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("disabled-agent", roles=["agent"], available=False),
        ],
        role_defaults={},
    )
    router = ModelRouter(ModelRegistry(config))

    with pytest.raises(RoutingError, match="No available model"):
        router.route("general_reasoning")


# ── Known task types ─────────────────────────────────────────────────


def test_known_task_types():
    task_types = ModelRouter.known_task_types()

    assert "general_reasoning" in task_types
    assert "drafting" in task_types
    assert "code_generation" in task_types
    assert "visual_reasoning" in task_types
    assert "image_analysis" in task_types


# ── Integration with repository defaults ─────────────────────────────


def test_router_with_repository_defaults():
    """Verify the router works with the actual config/models.yaml."""
    from aegis.config import load_config

    config = load_config()
    registry = ModelRegistry(config.models)
    router = ModelRouter(registry)

    for task_type in ("general_reasoning", "drafting", "code_generation", "visual_reasoning", "image_analysis"):
        decision = router.route(task_type)
        assert decision.model_id
        assert decision.provider_id
        assert decision.role
        assert len(decision.reason) > 0


# ── 1. Correct task-to-model routing & Determinism ───────────────────


def test_routing_is_strictly_deterministic_across_repeated_invocations():
    router = _standard_router()
    decisions = [router.route("code_generation") for _ in range(25)]

    first = decisions[0]
    for d in decisions[1:]:
        assert d.model_id == first.model_id
        assert d.provider_id == first.provider_id
        assert d.role == first.role
        assert d.reason == first.reason
        assert d.fallback == first.fallback


@pytest.mark.parametrize(
    ("task_type", "expected_role", "expected_model"),
    [
        ("general_reasoning", "agent", "agent-model"),
        ("drafting", "agent", "agent-model"),
        ("code_generation", "coding", "coding-model"),
        ("visual_reasoning", "vision", "vision-model"),
        ("image_analysis", "vision", "vision-model"),
    ],
)
def test_all_standard_task_types_route_to_expected_model(
    task_type: str, expected_role: str, expected_model: str
):
    decision = _standard_router().route(task_type)
    assert decision.role == expected_role
    assert decision.model_id == expected_model
    assert decision.provider_id == "local"


# ── 2. Unsupported capability ─────────────────────────────────────────


def test_unsupported_task_type_raises_routing_error():
    router = _standard_router()
    with pytest.raises(RoutingError) as exc_info:
        router.route("audio_transcription")
    assert "No routing rule for task type 'audio_transcription'" in str(exc_info.value)


def test_unsupported_required_capability_raises_routing_error():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model(
                "agent-model",
                roles=["agent"],
                capabilities=["text_generation", "reasoning"],
            ),
        ],
        role_defaults={"agent": "agent-model"},
    )
    router = ModelRouter(ModelRegistry(config))

    with pytest.raises(RoutingError) as exc_info:
        router.route("general_reasoning", required_capability="quantum_synthesis")
    assert "supports required capability 'quantum_synthesis'" in str(exc_info.value)


def test_required_capability_filters_out_incapable_default_to_capable_model():
    """If default model lacks the capability but an alternative has it, route to the capable model."""
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model(
                "standard-agent",
                roles=["agent"],
                capabilities=["text_generation"],
            ),
            _make_model(
                "advanced-agent",
                roles=["agent"],
                capabilities=["text_generation", "structured_planning"],
            ),
        ],
        role_defaults={"agent": "standard-agent"},
    )
    router = ModelRouter(ModelRegistry(config))
    decision = router.route("general_reasoning", required_capability="structured_planning")

    assert decision.model_id == "advanced-agent"
    assert "fallback model (default lacks capability 'structured_planning')" in decision.reason
    assert "capability: structured_planning" in decision.reason


# ── 3. Unavailable model ─────────────────────────────────────────────


def test_model_with_available_false_is_excluded():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("agent-1", roles=["agent"], available=False),
            _make_model("agent-2", roles=["agent"], available=True),
        ],
        role_defaults={"agent": "agent-2"},
    )
    registry = ModelRegistry(config)
    assert len(registry.get_models_for_role("agent")) == 1
    assert registry.get_models_for_role("agent")[0].id == "agent-2"


def test_model_with_enabled_false_is_excluded():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("agent-1", roles=["agent"], enabled=False),
            _make_model("agent-2", roles=["agent"], enabled=True),
        ],
        role_defaults={"agent": "agent-2"},
    )
    registry = ModelRegistry(config)
    assert len(registry.get_models_for_role("agent")) == 1
    assert registry.get_models_for_role("agent")[0].id == "agent-2"


def test_model_with_unavailable_health_is_excluded():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("agent-1", roles=["agent"], health=ModelHealth.UNAVAILABLE),
            _make_model("agent-2", roles=["agent"], health=ModelHealth.HEALTHY),
        ],
        role_defaults={"agent": "agent-2"},
    )
    registry = ModelRegistry(config)
    assert len(registry.get_models_for_role("agent")) == 1
    assert registry.get_models_for_role("agent")[0].id == "agent-2"


def test_all_models_unavailable_raises_routing_error():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("agent-1", roles=["agent"], available=False),
            _make_model("agent-2", roles=["agent"], health=ModelHealth.UNAVAILABLE),
        ],
        role_defaults={},
    )
    router = ModelRouter(ModelRegistry(config))
    with pytest.raises(RoutingError) as exc_info:
        router.route("general_reasoning")
    assert "No available model for role 'agent'" in str(exc_info.value)


# ── 4. Fallback handling ─────────────────────────────────────────────


def test_fallback_info_details():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("primary", roles=["coding"]),
            _make_model("secondary", roles=["coding"]),
        ],
        role_defaults={"coding": "primary"},
    )
    router = ModelRouter(ModelRegistry(config))
    decision = router.route("code_generation")

    assert decision.model_id == "primary"
    assert decision.fallback is not None
    assert decision.fallback.model_id == "secondary"
    assert decision.fallback.provider_id == "local"
    assert "Alternative model for role 'coding'" in decision.fallback.reason


def test_fallback_when_default_model_becomes_unhealthy():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model("primary", roles=["vision"], health=ModelHealth.UNAVAILABLE),
            _make_model("secondary", roles=["vision"], health=ModelHealth.HEALTHY),
        ],
        role_defaults={"vision": "secondary"},
    )
    registry = ModelRegistry(config)
    # Simulate default configured was primary, but primary is unhealthy
    registry._role_defaults["vision"] = "primary"

    router = ModelRouter(registry)
    decision = router.route("visual_reasoning")

    assert decision.model_id == "secondary"
    assert "fallback model" in decision.reason


# ── 5. Malformed registry entry validation ────────────────────────────


@pytest.mark.parametrize(
    "invalid_id",
    ["", "Model With Spaces", "model@symbol", "-leading-hyphen", "UPPERCASE_MODEL"],
)
def test_malformed_registry_invalid_model_id_rejected(invalid_id: str):
    with pytest.raises(ValidationError):
        ModelConfig(id=invalid_id, provider="local", roles=["agent"])


def test_malformed_registry_missing_required_fields():
    with pytest.raises(ValidationError):
        ModelConfig.model_validate({"provider": "local"})  # missing id and roles


def test_malformed_registry_duplicate_roles_rejected():
    with pytest.raises(ValidationError, match="roles"):
        ModelConfig(id="m1", provider="local", roles=["agent", "agent"])


def test_malformed_registry_unknown_provider_reference_rejected():
    with pytest.raises(ValidationError, match="references unknown provider"):
        ModelRegistryConfig(
            providers=[_make_provider("local_prov")],
            models=[_make_model("m1", provider="nonexistent_provider")],
        )


def test_malformed_registry_enabled_model_disabled_provider_rejected():
    with pytest.raises(ValidationError, match="cannot use disabled provider"):
        ModelRegistryConfig(
            providers=[_make_provider("prov1", enabled=False, kind="local", endpoint="http://localhost:11434/v1")],
            models=[_make_model("m1", provider="prov1", enabled=True)],
        )


def test_malformed_registry_duplicate_model_ids_rejected():
    with pytest.raises(ValidationError, match="model ids must be unique"):
        ModelRegistryConfig(
            providers=[_make_provider()],
            models=[
                _make_model("m1", roles=["agent"]),
                _make_model("m1", roles=["coding"]),
            ],
        )


def test_malformed_registry_role_default_unknown_model_rejected():
    with pytest.raises(ValidationError, match="references unknown model"):
        ModelRegistryConfig(
            providers=[_make_provider()],
            models=[_make_model("m1", roles=["agent"])],
            role_defaults={"agent": "ghost_model"},
        )


def test_malformed_registry_role_default_role_mismatch_rejected():
    with pytest.raises(ValidationError, match="must reference a model that declares that role"):
        ModelRegistryConfig(
            providers=[_make_provider()],
            models=[_make_model("m1", roles=["coding"])],
            role_defaults={"agent": "m1"},
        )


def test_router_raises_routing_error_if_model_provider_disappears_at_runtime():
    """If a model references a provider missing from the registry, route() raises RoutingError."""
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[_make_model("m1", roles=["agent"])],
        role_defaults={"agent": "m1"},
    )
    registry = ModelRegistry(config)
    # Clear the providers map at runtime to simulate provider disappearance
    registry._providers_by_id.clear()

    router = ModelRouter(registry)
    with pytest.raises(RoutingError, match="references unknown provider"):
        router.route("general_reasoning")


# ── 6. Auditable routing reason ───────────────────────────────────────


def test_auditable_routing_reason_format_and_traceability():
    decision = _standard_router().route("code_generation", modality="spreadsheet")

    assert decision.reason.startswith("Routed via task type 'code_generation'")
    assert "to default model for role 'coding'" in decision.reason
    assert "'coding-model' on provider 'local'" in decision.reason
    assert "(modality: spreadsheet)" in decision.reason


def test_auditable_routing_reason_with_role_override_and_capability():
    config = ModelRegistryConfig(
        providers=[_make_provider()],
        models=[
            _make_model(
                "agent-model",
                roles=["agent"],
                capabilities=["text_generation", "drafting"],
            ),
        ],
        role_defaults={"agent": "agent-model"},
    )
    router = ModelRouter(ModelRegistry(config))
    decision = router.route(
        "drafting",
        role="agent",
        modality="text",
        required_capability="drafting",
    )

    assert "explicit role override 'agent'" in decision.reason
    assert "default model for role 'agent'" in decision.reason
    assert "capability: drafting" in decision.reason
    assert "modality: text" in decision.reason


def test_routing_decision_immutability_prevents_tampering():
    decision = _standard_router().route("general_reasoning")

    with pytest.raises(AttributeError):
        decision.reason = "modified reason"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        decision.provider_id = "external_cloud"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        decision.role = "compromised"  # type: ignore[misc]

