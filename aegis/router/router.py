"""Deterministic, explainable model router for the AEGIS prototype.

The router maps task types and explicit role overrides to registered models
using purely deterministic rules. No learned, semantic, or ML-based routing
is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.config import ModelConfig

from .registry import ModelRegistry


# ── Deterministic task-type → role mapping ───────────────────────────

_TASK_TYPE_TO_ROLE: dict[str, str] = {
    "general_reasoning": "agent",
    "drafting": "agent",
    "code_generation": "coding",
    "visual_reasoning": "vision",
    "image_analysis": "vision",
}


class RoutingError(ValueError):
    """Raised when the router cannot resolve a model for the request."""


@dataclass(frozen=True)
class FallbackInfo:
    """Alternative model information when the primary is unavailable."""

    model_id: str
    provider_id: str
    reason: str


@dataclass(frozen=True)
class RoutingDecision:
    """Complete, explainable result of a deterministic routing decision."""

    model_id: str
    provider_id: str
    role: str
    reason: str
    fallback: FallbackInfo | None = None


class ModelRouter:
    """Deterministic, rule-based model router.

    Routing rules:
    - ``general_reasoning`` / ``drafting`` → role ``agent``
    - ``code_generation`` → role ``coding``
    - ``visual_reasoning`` / ``image_analysis`` → role ``vision``

    An explicit *role* parameter always takes precedence over the task-type
    mapping. The router selects the configured default model for the resolved
    role, falling back to any other available model for that role when the
    default is unavailable.
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    @staticmethod
    def known_task_types() -> tuple[str, ...]:
        """Return the set of task types with deterministic routing rules."""
        return tuple(_TASK_TYPE_TO_ROLE.keys())

    def route(
        self,
        task_type: str,
        *,
        role: str | None = None,
        modality: str | None = None,
        required_capability: str | None = None,
    ) -> RoutingDecision:
        """Select a model for the given *task_type* and return an explainable decision.

        Parameters
        ----------
        task_type:
            The logical task type driving model selection (e.g. ``"code_generation"``).
        role:
            Optional explicit role override. When provided, the task-type mapping
            is bypassed and this role is used directly.
        modality:
            Optional modality hint for future extensibility.
        required_capability:
            Optional specific capability requirement (e.g. ``"code_generation"``).
            When specified, only models that declare this capability are considered.

        Returns
        -------
        RoutingDecision
            The selected model, its provider, the resolved role, a human-readable
            routing reason, and optional fallback information.

        Raises
        ------
        RoutingError
            When the resolved role has no available models in the registry,
            or no available model supports the required capability.
        """

        # ── Resolve role ─────────────────────────────────────────────
        if role is not None:
            resolved_role = role
            role_source = f"explicit role override '{role}'"
        else:
            resolved_role = _TASK_TYPE_TO_ROLE.get(task_type)
            if resolved_role is None:
                raise RoutingError(
                    f"No routing rule for task type '{task_type}' "
                    f"and no explicit role was provided."
                )
            role_source = f"task type '{task_type}'"

        # ── Filter candidates by role ───────────────────────────────
        default_model = self._registry.get_default_model_for_role(resolved_role)
        candidates = list(self._registry.get_models_for_role(resolved_role))

        # ── Capability filtering ────────────────────────────────────
        if required_capability is not None:
            capable_candidates = [
                m for m in candidates if required_capability in m.capabilities
            ]
            if not capable_candidates:
                raise RoutingError(
                    f"No available model for role '{resolved_role}' "
                    f"(resolved from {role_source}) "
                    f"supports required capability '{required_capability}'."
                )
            if default_model is not None and required_capability in default_model.capabilities:
                selected = default_model
                selection_detail = f"default model for role '{resolved_role}'"
            else:
                selected = capable_candidates[0]
                if default_model is not None:
                    selection_detail = (
                        f"fallback model (default lacks capability '{required_capability}') "
                        f"for role '{resolved_role}'"
                    )
                else:
                    selection_detail = (
                        f"fallback model (default unavailable) for role '{resolved_role}'"
                    )
            candidates = capable_candidates
        else:
            if default_model is not None:
                selected = default_model
                selection_detail = f"default model for role '{resolved_role}'"
            elif candidates:
                selected = candidates[0]
                selection_detail = (
                    f"fallback model (default unavailable) for role '{resolved_role}'"
                )
            else:
                raise RoutingError(
                    f"No available model for role '{resolved_role}' "
                    f"(resolved from {role_source})."
                )

        # ── Provider ────────────────────────────────────────────────
        provider = self._registry.get_provider_for_model(selected.id)
        if provider is None:
            raise RoutingError(
                f"Model '{selected.id}' references unknown provider '{selected.provider}'."
            )

        # ── Build reason ────────────────────────────────────────────
        reason_parts = [
            f"Routed via {role_source}",
            f"to {selection_detail}",
            f"'{selected.id}' on provider '{provider.id}'",
        ]
        hints: list[str] = []
        if required_capability is not None:
            hints.append(f"capability: {required_capability}")
        if modality is not None:
            hints.append(f"modality: {modality}")

        reason = " → ".join(reason_parts)
        if hints:
            reason += f" ({', '.join(hints)})"

        # ── Fallback info ───────────────────────────────────────────
        fallback: FallbackInfo | None = None
        alternatives = [m for m in candidates if m.id != selected.id]
        if alternatives:
            alt = alternatives[0]
            alt_provider = self._registry.get_provider_for_model(alt.id)
            if alt_provider is not None:
                fallback = FallbackInfo(
                    model_id=alt.id,
                    provider_id=alt_provider.id,
                    reason=f"Alternative model for role '{resolved_role}'",
                )

        return RoutingDecision(
            model_id=selected.id,
            provider_id=provider.id,
            role=resolved_role,
            reason=reason,
            fallback=fallback,
        )
