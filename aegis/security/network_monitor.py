"""Provider-independent NetworkMonitor interface and implementations.

Provides:
- NetworkMonitor: Provider-independent abstract base interface.
- StandardNetworkMonitor: Concrete monitor with pluggable collector and ring-buffer storage.
- AuthorizedNetworkMonitor: RBAC-enforcing facade requiring ACCESS_NETWORK_MONITOR.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
import threading
from typing import Sequence

from aegis.auth.authorization import Permission, require_permission
from aegis.auth.models import UserIdentity

from .collector import InMemoryNetworkCollector, NetworkCollector
from .network_models import (
    NetworkClassification,
    NetworkObservation,
    NetworkPolicy,
    NetworkSummary,
    PolicyViolation,
    TrafficDirection,
    TrafficStatus,
)


class NetworkMonitor(ABC):
    """Provider-independent NetworkMonitor interface.

    Allows inspecting, collecting, and querying network observations across
    local prototype development, sandbox execution, and production deployments.
    """

    @abstractmethod
    def get_observations(
        self,
        classification: NetworkClassification | None = None,
        direction: TrafficDirection | None = None,
        status: TrafficStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[NetworkObservation]:
        """Obtain observed network events matching optional query filters."""

    @abstractmethod
    def record_observation(self, observation: NetworkObservation) -> None:
        """Record an observed network event into the monitor's store."""

    @abstractmethod
    def collect_now(self) -> list[NetworkObservation]:
        """Trigger an immediate collection cycle via the underlying collector."""

    @abstractmethod
    def get_summary(self) -> NetworkSummary:
        """Return aggregated summary metrics of observed traffic."""

    @abstractmethod
    def verify_policy(self, policy: NetworkPolicy | None = None) -> list[PolicyViolation]:
        """Check observed traffic against network policy without fabricating events."""


class StandardNetworkMonitor(NetworkMonitor):
    """Standard in-memory network monitor with replaceable collector.

    Maintains a thread-safe bounded history of network observations, supports
    rich filtering, and performs policy validation against observed traffic.
    """

    def __init__(
        self,
        collector: NetworkCollector | None = None,
        max_history: int = 5000,
        default_policy: NetworkPolicy | None = None,
    ) -> None:
        self._collector: NetworkCollector = collector or InMemoryNetworkCollector()
        self._max_history = max_history
        self._policy = default_policy or NetworkPolicy()
        self._history: deque[NetworkObservation] = deque(maxlen=max_history)
        self._lock = threading.Lock()

    @property
    def collector(self) -> NetworkCollector:
        return self._collector

    @collector.setter
    def collector(self, new_collector: NetworkCollector) -> None:
        with self._lock:
            self._collector = new_collector

    def record_observation(self, observation: NetworkObservation) -> None:
        """Record an observed network event into the bounded buffer."""
        with self._lock:
            self._history.append(observation)

    def record_observations(self, observations: Sequence[NetworkObservation]) -> None:
        """Record multiple observed network events into the bounded buffer."""
        with self._lock:
            self._history.extend(observations)

    def collect_now(self) -> list[NetworkObservation]:
        """Execute a collection cycle from the configured collector and record observations."""
        new_obs = self._collector.collect()
        self.record_observations(new_obs)
        return new_obs

    def get_observations(
        self,
        classification: NetworkClassification | None = None,
        direction: TrafficDirection | None = None,
        status: TrafficStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[NetworkObservation]:
        """Retrieve observations with optional filtering."""
        with self._lock:
            # Snapshot history to list
            records = list(self._history)

        filtered: list[NetworkObservation] = []
        for obs in reversed(records):  # Newest first
            if classification and obs.classification != classification:
                continue
            if direction and obs.direction != direction:
                continue
            if status and obs.status != status:
                continue
            if since and obs.timestamp < since:
                continue
            filtered.append(obs)
            if limit and len(filtered) >= limit:
                break

        return filtered

    def get_summary(self) -> NetworkSummary:
        """Compute aggregated statistics across all currently recorded observations."""
        with self._lock:
            records = list(self._history)

        if not records:
            return NetworkSummary()

        internal = sum(1 for o in records if o.classification == NetworkClassification.INTERNAL)
        external = sum(1 for o in records if o.classification == NetworkClassification.EXTERNAL)
        blocked = sum(1 for o in records if o.classification == NetworkClassification.BLOCKED)
        unknown = sum(1 for o in records if o.classification == NetworkClassification.UNKNOWN)

        inbound = sum(1 for o in records if o.direction == TrafficDirection.INBOUND)
        outbound = sum(1 for o in records if o.direction == TrafficDirection.OUTBOUND)
        loopback = sum(1 for o in records if o.direction == TrafficDirection.LOOPBACK)

        violations = len(self.verify_policy(self._policy))

        first_ts = min(o.timestamp for o in records)
        last_ts = max(o.timestamp for o in records)

        return NetworkSummary(
            total_observations=len(records),
            internal_count=internal,
            external_count=external,
            blocked_count=blocked,
            unknown_count=unknown,
            inbound_count=inbound,
            outbound_count=outbound,
            loopback_count=loopback,
            policy_violations=violations,
            first_observed=first_ts,
            last_observed=last_ts,
        )

    def verify_policy(self, policy: NetworkPolicy | None = None) -> list[PolicyViolation]:
        """Evaluate observed traffic against configured network policy.

        Strict Invariant: This assesses compliance of observed events. It never
        fabricates fake blocked events.
        """
        active_policy = policy or self._policy
        with self._lock:
            records = list(self._history)

        violations: list[PolicyViolation] = []
        for obs in records:
            # 1. Check external egress policy
            if not active_policy.allow_external and obs.classification == NetworkClassification.EXTERNAL:
                violations.append(
                    PolicyViolation(
                        observation_id=obs.observation_id,
                        policy_id=active_policy.policy_id,
                        reason=f"Observed external traffic to {obs.destination}:{obs.destination_port} violates no-egress policy",
                        observation=obs,
                    )
                )
                continue

            # 2. Check sandbox isolation policy
            if (
                active_policy.require_sandbox_isolation
                and obs.container
                and obs.classification == NetworkClassification.EXTERNAL
            ):
                violations.append(
                    PolicyViolation(
                        observation_id=obs.observation_id,
                        policy_id=active_policy.policy_id,
                        reason=f"Sandbox container '{obs.container}' emitted external traffic to {obs.destination}",
                        observation=obs,
                    )
                )
                continue

            # 3. Check allowed ports if restricted
            if active_policy.allowed_destination_ports is not None and obs.destination_port:
                if obs.destination_port not in active_policy.allowed_destination_ports:
                    violations.append(
                        PolicyViolation(
                            observation_id=obs.observation_id,
                            policy_id=active_policy.policy_id,
                            reason=f"Destination port {obs.destination_port} is not in allowed policy ports",
                            observation=obs,
                        )
                    )
                    continue

            # 4. Check allowed protocols
            if active_policy.allowed_protocols and obs.protocol.upper() not in [
                p.upper() for p in active_policy.allowed_protocols
            ]:
                violations.append(
                    PolicyViolation(
                        observation_id=obs.observation_id,
                        policy_id=active_policy.policy_id,
                        reason=f"Protocol {obs.protocol} is not permitted by policy",
                        observation=obs,
                    )
                )

        return violations

    def clear(self) -> None:
        """Clear recorded observation buffer."""
        with self._lock:
            self._history.clear()


class AuthorizedNetworkMonitor:
    """RBAC-enforcing facade wrapping a :class:`NetworkMonitor`.

    Restricts network monitoring visibility to callers possessing the
    ``ACCESS_NETWORK_MONITOR`` permission (assigned to ADMIN role).
    """

    def __init__(self, monitor: NetworkMonitor) -> None:
        self._inner = monitor

    def get_observations(
        self,
        user: UserIdentity,
        classification: NetworkClassification | None = None,
        direction: TrafficDirection | None = None,
        status: TrafficStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[NetworkObservation]:
        """Obtain network observations after checking ACCESS_NETWORK_MONITOR."""
        require_permission(user, Permission.ACCESS_NETWORK_MONITOR)
        return self._inner.get_observations(
            classification=classification,
            direction=direction,
            status=status,
            since=since,
            limit=limit,
        )

    def collect_now(self, user: UserIdentity) -> list[NetworkObservation]:
        """Trigger collection cycle after checking ACCESS_NETWORK_MONITOR."""
        require_permission(user, Permission.ACCESS_NETWORK_MONITOR)
        return self._inner.collect_now()

    def get_summary(self, user: UserIdentity) -> NetworkSummary:
        """Retrieve aggregated summary metrics after checking ACCESS_NETWORK_MONITOR."""
        require_permission(user, Permission.ACCESS_NETWORK_MONITOR)
        return self._inner.get_summary()

    def verify_policy(
        self,
        user: UserIdentity,
        policy: NetworkPolicy | None = None,
    ) -> list[PolicyViolation]:
        """Evaluate policy compliance after checking ACCESS_NETWORK_MONITOR."""
        require_permission(user, Permission.ACCESS_NETWORK_MONITOR)
        return self._inner.verify_policy(policy=policy)
