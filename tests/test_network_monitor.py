"""Synthetic and unit tests for Phase 6.X Network Monitoring Abstraction.

Tests:
- Classification rules (INTERNAL, EXTERNAL, BLOCKED, UNKNOWN) across various endpoints
- Traffic direction inference (LOOPBACK, INBOUND, OUTBOUND, UNKNOWN)
- Clear distinction between observed traffic, configured policy, and actually observed blocked traffic
- Invariant: No fabricated blocked events
- NetworkCollector implementations (InMemoryNetworkCollector, LocalConnectionCollector)
- NetworkMonitor interface (filtering, observation recording, metrics summary)
- NetworkPolicy evaluation and violation detection
- AuthorizedNetworkMonitor RBAC enforcement (ADMIN allowed, USER denied)
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from aegis.auth.exceptions import AuthorizationError
from aegis.auth.models import UserIdentity, UserRole
from aegis.security import (
    AuthorizedNetworkMonitor,
    InMemoryNetworkCollector,
    LocalConnectionCollector,
    NetworkClassification,
    NetworkCollector,
    NetworkMonitor,
    NetworkObservation,
    NetworkPolicy,
    NetworkSummary,
    PolicyViolation,
    StandardNetworkMonitor,
    TrafficDirection,
    TrafficStatus,
    classify_destination,
    determine_traffic_direction,
    is_internal_ip,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# 1. Classification Tests (INTERNAL, EXTERNAL, BLOCKED, UNKNOWN)
# =====================================================================


class TestClassification:
    """Synthetic tests for deterministic offline destination classification."""

    @pytest.mark.parametrize(
        "dest",
        [
            "127.0.0.1",
            "127.0.0.2",
            "127.0.1.1",
            "::1",
            "127.0.0.1:8080",
            "[::1]:9000",
            "localhost",
            "localhost.localdomain",
            "api.internal",
            "database.local",
            "cluster.lan",
            "test.corp",
        ],
    )
    def test_classify_loopback_and_local_hostnames(self, dest: str):
        result = classify_destination(dest)
        assert result == NetworkClassification.INTERNAL

    @pytest.mark.parametrize(
        "dest",
        [
            # RFC 1918 Class A (10.0.0.0/8)
            "10.0.0.1",
            "10.254.1.99",
            "10.0.0.1:443",
            # RFC 1918 Class B (172.16.0.0/12, including Docker default bridge 172.17.0.0/16)
            "172.16.0.1",
            "172.17.0.2",
            "172.20.10.5:8000",
            "172.31.255.254",
            # RFC 1918 Class C (192.168.0.0/16)
            "192.168.1.1",
            "192.168.0.100:5432",
            # Link-local
            "169.254.169.254",
            "fe80::1",
        ],
    )
    def test_classify_private_subnets(self, dest: str):
        result = classify_destination(dest)
        assert result == NetworkClassification.INTERNAL

    @pytest.mark.parametrize(
        "dest",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "140.82.121.3",
            "8.8.8.8:53",
            "api.openai.com",
            "huggingface.co",
            "raw.githubusercontent.com",
            "telemetry.cloud.example.org",
        ],
    )
    def test_classify_external_endpoints(self, dest: str):
        result = classify_destination(dest)
        assert result == NetworkClassification.EXTERNAL

    @pytest.mark.parametrize(
        "dest",
        [
            "",
            "   ",
            "invalid_hostname_without_tld",
            "not an ip or domain",
            "???###",
        ],
    )
    def test_classify_unknown_endpoints(self, dest: str):
        result = classify_destination(dest)
        assert result == NetworkClassification.UNKNOWN

    def test_classify_actually_observed_blocked_traffic(self):
        """When an actual blocked packet/connection is observed, it classifies as BLOCKED."""
        # Even if destination is external or internal, observed blocked status yields BLOCKED
        result_ext = classify_destination("8.8.8.8", status=TrafficStatus.BLOCKED)
        assert result_ext == NetworkClassification.BLOCKED

        result_int = classify_destination("10.0.0.1", status=TrafficStatus.BLOCKED)
        assert result_int == NetworkClassification.BLOCKED

        result_unk = classify_destination("invalid_host", status=TrafficStatus.BLOCKED)
        assert result_unk == NetworkClassification.BLOCKED

    def test_custom_internal_cidrs(self):
        """Custom configured internal subnet ranges are respected."""
        custom_subnet = "52.0.0.0/16"
        # Without custom cidr, treated as external
        assert classify_destination("52.0.1.1") == NetworkClassification.EXTERNAL
        # With custom cidr, treated as internal
        assert (
            classify_destination("52.0.1.1", internal_cidrs=[custom_subnet])
            == NetworkClassification.INTERNAL
        )


# =====================================================================
# 2. Traffic Direction Inference Tests
# =====================================================================


class TestTrafficDirection:
    """Tests for determining traffic direction."""

    def test_loopback_direction(self):
        assert determine_traffic_direction("127.0.0.1:5000", "127.0.0.1:8000") == TrafficDirection.LOOPBACK
        assert determine_traffic_direction("localhost", "localhost") == TrafficDirection.LOOPBACK

    def test_outbound_direction(self):
        assert determine_traffic_direction("192.168.1.50", "8.8.8.8") == TrafficDirection.OUTBOUND
        assert determine_traffic_direction("10.0.0.5", "api.openai.com") == TrafficDirection.OUTBOUND

    def test_inbound_direction(self):
        assert determine_traffic_direction("8.8.8.8", "192.168.1.50") == TrafficDirection.INBOUND

    def test_missing_endpoints_returns_unknown(self):
        assert determine_traffic_direction(None, "127.0.0.1") == TrafficDirection.UNKNOWN
        assert determine_traffic_direction("127.0.0.1", None) == TrafficDirection.UNKNOWN
        assert determine_traffic_direction("", "") == TrafficDirection.UNKNOWN


# =====================================================================
# 3. Policy vs Observation vs Actually Blocked Invariants
# =====================================================================


class TestPolicyVsObservationDistinction:
    """Tests guaranteeing distinction between observed traffic, policy, and blocked events."""

    def test_policy_does_not_fabricate_blocked_network_events(self):
        """Observed external traffic is NOT fabricated into a blocked observation."""
        policy = NetworkPolicy(allow_external=False)
        collector = InMemoryNetworkCollector()

        # Record real observed active outbound connection
        obs = collector.add_synthetic(
            source="192.168.1.10",
            destination="8.8.8.8",
            destination_port=53,
            protocol="UDP",
            status=TrafficStatus.ALLOWED,  # Actually allowed/active on wire
        )

        monitor = StandardNetworkMonitor(collector=collector, default_policy=policy)
        monitor.record_observation(obs)

        # The observation retains its real observed status
        stored = monitor.get_observations()
        assert len(stored) == 1
        assert stored[0].status == TrafficStatus.ALLOWED
        assert stored[0].classification == NetworkClassification.EXTERNAL
        assert stored[0].status != TrafficStatus.BLOCKED

        # The policy evaluation flags a violation WITHOUT modifying or inventing events
        violations = monitor.verify_policy(policy)
        assert len(violations) == 1
        assert "violates no-egress policy" in violations[0].reason
        assert violations[0].observation_id == obs.observation_id

    def test_blocked_status_only_recorded_when_actually_observed(self):
        """An observation is marked BLOCKED only when actually observed as blocked."""
        # Scenario: Docker socket returns EPERM / connection refused in sandbox
        blocked_obs = NetworkObservation(
            source="172.17.0.2",
            destination="8.8.8.8",
            destination_port=443,
            protocol="TCP",
            direction=TrafficDirection.OUTBOUND,
            classification=NetworkClassification.BLOCKED,
            container="aegis-sandbox-task-1",
            status=TrafficStatus.BLOCKED,
            metadata={"reason": "ECONNREFUSED_NETWORK_NONE"},
        )

        assert blocked_obs.status == TrafficStatus.BLOCKED
        assert blocked_obs.classification == NetworkClassification.BLOCKED
        assert blocked_obs.process_or_container == "aegis-sandbox-task-1"

    def test_observation_immutability(self):
        obs = NetworkObservation(
            source="127.0.0.1",
            destination="127.0.0.1",
            destination_port=8000,
            direction=TrafficDirection.LOOPBACK,
            classification=NetworkClassification.INTERNAL,
        )
        with pytest.raises(Exception):
            # Frozen Pydantic model cannot be mutated
            obs.destination = "8.8.8.8"  # type: ignore[misc]


# =====================================================================
# 4. Collector Implementations
# =====================================================================


class TestCollectors:
    """Tests for InMemoryNetworkCollector and LocalConnectionCollector."""

    def test_in_memory_collector_feed(self):
        collector = InMemoryNetworkCollector()
        assert collector.collect() == []

        collector.add_synthetic(
            source="127.0.0.1",
            destination="127.0.0.1",
            destination_port=8080,
            process="uvicorn",
        )
        collector.add_synthetic(
            source="10.0.0.2",
            destination="8.8.8.8",
            destination_port=443,
            process="curl",
        )

        results = collector.collect()
        assert len(results) == 2
        assert results[0].classification == NetworkClassification.INTERNAL
        assert results[1].classification == NetworkClassification.EXTERNAL
        assert results[0].process == "uvicorn"
        assert results[1].process == "curl"

        collector.clear()
        assert collector.collect() == []

    def test_local_connection_collector_runs_without_error(self):
        """LocalConnectionCollector executes in the local environment without crashing."""
        collector = LocalConnectionCollector()
        observations = collector.collect()

        assert isinstance(observations, list)
        for obs in observations:
            assert isinstance(obs, NetworkObservation)
            assert obs.source
            assert obs.destination
            assert obs.classification in (
                NetworkClassification.INTERNAL,
                NetworkClassification.EXTERNAL,
                NetworkClassification.BLOCKED,
                NetworkClassification.UNKNOWN,
            )
            # Must not fabricate blocked events
            assert obs.status in (TrafficStatus.OBSERVED, TrafficStatus.ALLOWED)

    def test_split_host_port_helper(self):
        assert LocalConnectionCollector._split_host_port("127.0.0.1:8000") == ("127.0.0.1", 8000)
        assert LocalConnectionCollector._split_host_port("[::1]:9000") == ("::1", 9000)
        assert LocalConnectionCollector._split_host_port("10.0.0.1") == ("10.0.0.1", None)
        assert LocalConnectionCollector._split_host_port("") == ("", None)


# =====================================================================
# 5. NetworkMonitor Interface & Querying
# =====================================================================


class TestNetworkMonitorInterface:
    """Tests for provider-independent NetworkMonitor query and filter behavior."""

    @pytest.fixture
    def populated_monitor(self) -> StandardNetworkMonitor:
        monitor = StandardNetworkMonitor()

        base_time = _now() - timedelta(minutes=10)

        # 1. Internal loopback
        monitor.record_observation(
            NetworkObservation(
                timestamp=base_time + timedelta(minutes=1),
                source="127.0.0.1",
                destination="127.0.0.1",
                destination_port=8000,
                protocol="TCP",
                direction=TrafficDirection.LOOPBACK,
                classification=NetworkClassification.INTERNAL,
                process="web-server",
                status=TrafficStatus.ALLOWED,
            )
        )
        # 2. External outbound
        monitor.record_observation(
            NetworkObservation(
                timestamp=base_time + timedelta(minutes=3),
                source="192.168.1.15",
                destination="93.184.216.34",
                destination_port=443,
                protocol="TCP",
                direction=TrafficDirection.OUTBOUND,
                classification=NetworkClassification.EXTERNAL,
                process="ollama",
                status=TrafficStatus.ALLOWED,
            )
        )
        # 3. Actually observed blocked event
        monitor.record_observation(
            NetworkObservation(
                timestamp=base_time + timedelta(minutes=5),
                source="172.17.0.3",
                destination="8.8.8.8",
                destination_port=53,
                protocol="UDP",
                direction=TrafficDirection.OUTBOUND,
                classification=NetworkClassification.BLOCKED,
                container="sandbox-c1",
                status=TrafficStatus.BLOCKED,
            )
        )
        # 4. Unknown endpoint
        monitor.record_observation(
            NetworkObservation(
                timestamp=base_time + timedelta(minutes=7),
                source="127.0.0.1",
                destination="unresolvable_endpoint_name",
                destination_port=9999,
                protocol="TCP",
                direction=TrafficDirection.UNKNOWN,
                classification=NetworkClassification.UNKNOWN,
                status=TrafficStatus.OBSERVED,
            )
        )
        return monitor

    def test_filter_by_classification(self, populated_monitor: StandardNetworkMonitor):
        internals = populated_monitor.get_observations(classification=NetworkClassification.INTERNAL)
        assert len(internals) == 1
        assert internals[0].destination == "127.0.0.1"

        externals = populated_monitor.get_observations(classification=NetworkClassification.EXTERNAL)
        assert len(externals) == 1
        assert externals[0].destination == "93.184.216.34"

        blocked = populated_monitor.get_observations(classification=NetworkClassification.BLOCKED)
        assert len(blocked) == 1
        assert blocked[0].container == "sandbox-c1"

    def test_filter_by_direction(self, populated_monitor: StandardNetworkMonitor):
        loopbacks = populated_monitor.get_observations(direction=TrafficDirection.LOOPBACK)
        assert len(loopbacks) == 1
        assert loopbacks[0].direction == TrafficDirection.LOOPBACK

        outbounds = populated_monitor.get_observations(direction=TrafficDirection.OUTBOUND)
        assert len(outbounds) == 2

    def test_filter_by_status(self, populated_monitor: StandardNetworkMonitor):
        blocked = populated_monitor.get_observations(status=TrafficStatus.BLOCKED)
        assert len(blocked) == 1
        assert blocked[0].status == TrafficStatus.BLOCKED

    def test_filter_by_limit_and_since(self, populated_monitor: StandardNetworkMonitor):
        limited = populated_monitor.get_observations(limit=2)
        assert len(limited) == 2

        # In populated_monitor, observations were added at base_time + 1, + 3, + 5, + 7
        # Fetching since base_time + 4 mins should yield the observations at + 5 and + 7 mins (2 items)
        records = populated_monitor.get_observations()
        base_time = min(r.timestamp for r in records)
        since_time = base_time + timedelta(minutes=4)
        recent = populated_monitor.get_observations(since=since_time)
        assert len(recent) == 2

    def test_summary_calculation(self, populated_monitor: StandardNetworkMonitor):
        summary = populated_monitor.get_summary()
        assert summary.total_observations == 4
        assert summary.internal_count == 1
        assert summary.external_count == 1
        assert summary.blocked_count == 1
        assert summary.unknown_count == 1
        assert summary.loopback_count == 1
        assert summary.outbound_count == 2
        assert summary.policy_violations == 1  # The unpermitted external egress
        assert summary.first_observed is not None
        assert summary.last_observed is not None

    def test_replaceable_collector_property(self):
        monitor = StandardNetworkMonitor()
        new_collector = InMemoryNetworkCollector()
        monitor.collector = new_collector
        assert monitor.collector is new_collector


# =====================================================================
# 6. Policy Enforcement and Sandbox Isolation Verification
# =====================================================================


class TestPolicyVerification:
    """Tests for checking observed traffic against sovereign network policy."""

    def test_policy_detects_egress_violation(self):
        policy = NetworkPolicy(allow_external=False)
        monitor = StandardNetworkMonitor(default_policy=policy)

        # Add external egress observation
        monitor.record_observation(
            NetworkObservation(
                source="192.168.1.10",
                destination="api.openai.com",
                destination_port=443,
                protocol="TCP",
                direction=TrafficDirection.OUTBOUND,
                classification=NetworkClassification.EXTERNAL,
            )
        )

        violations = monitor.verify_policy(policy)
        assert len(violations) == 1
        assert "no-egress policy" in violations[0].reason

    def test_policy_detects_sandbox_container_egress(self):
        policy = NetworkPolicy(allow_external=True, require_sandbox_isolation=True)
        monitor = StandardNetworkMonitor(default_policy=policy)

        # Add container egress observation
        monitor.record_observation(
            NetworkObservation(
                source="172.17.0.2",
                destination="8.8.8.8",
                destination_port=53,
                protocol="UDP",
                direction=TrafficDirection.OUTBOUND,
                classification=NetworkClassification.EXTERNAL,
                container="sandbox-worker-1",
            )
        )

        violations = monitor.verify_policy(policy)
        assert len(violations) == 1
        assert "Sandbox container 'sandbox-worker-1' emitted external traffic" in violations[0].reason

    def test_policy_detects_unauthorized_destination_port(self):
        policy = NetworkPolicy(
            allow_external=True,
            allowed_destination_ports=[80, 443],
        )
        monitor = StandardNetworkMonitor(default_policy=policy)

        monitor.record_observation(
            NetworkObservation(
                source="192.168.1.10",
                destination="93.184.216.34",
                destination_port=8080,  # Not in [80, 443]
                protocol="TCP",
                classification=NetworkClassification.EXTERNAL,
            )
        )

        violations = monitor.verify_policy(policy)
        assert len(violations) == 1
        assert "Destination port 8080 is not in allowed policy ports" in violations[0].reason

    def test_policy_compliant_internal_traffic_yields_zero_violations(self):
        policy = NetworkPolicy(allow_external=False)
        monitor = StandardNetworkMonitor(default_policy=policy)

        monitor.record_observation(
            NetworkObservation(
                source="127.0.0.1",
                destination="127.0.0.1",
                destination_port=8000,
                protocol="TCP",
                direction=TrafficDirection.LOOPBACK,
                classification=NetworkClassification.INTERNAL,
            )
        )
        monitor.record_observation(
            NetworkObservation(
                source="10.0.0.5",
                destination="10.0.0.6",
                destination_port=5432,
                protocol="TCP",
                direction=TrafficDirection.LOOPBACK,
                classification=NetworkClassification.INTERNAL,
            )
        )

        violations = monitor.verify_policy(policy)
        assert len(violations) == 0


# =====================================================================
# 7. AuthorizedNetworkMonitor RBAC Tests
# =====================================================================


class TestAuthorizedNetworkMonitor:
    """Tests ensuring network monitor access is restricted by RBAC permissions."""

    @pytest.fixture
    def monitor_and_users(self):
        inner_monitor = StandardNetworkMonitor()
        inner_monitor.record_observation(
            NetworkObservation(
                source="127.0.0.1",
                destination="127.0.0.1",
                destination_port=8000,
                protocol="TCP",
                direction=TrafficDirection.LOOPBACK,
                classification=NetworkClassification.INTERNAL,
            )
        )
        auth_monitor = AuthorizedNetworkMonitor(inner_monitor)

        admin_user = UserIdentity(
            user_id="admin-1",
            username="admin",
            role=UserRole.ADMIN,
            display_name="Admin User",
        )
        regular_user = UserIdentity(
            user_id="user-1",
            username="user",
            role=UserRole.USER,
            display_name="Regular User",
        )
        return auth_monitor, admin_user, regular_user

    def test_admin_can_access_network_monitor(self, monitor_and_users):
        auth_monitor, admin_user, _ = monitor_and_users

        observations = auth_monitor.get_observations(user=admin_user)
        assert len(observations) == 1

        summary = auth_monitor.get_summary(user=admin_user)
        assert summary.total_observations == 1

        violations = auth_monitor.verify_policy(user=admin_user)
        assert isinstance(violations, list)

        collected = auth_monitor.collect_now(user=admin_user)
        assert isinstance(collected, list)

    def test_regular_user_denied_access_to_network_monitor(self, monitor_and_users):
        auth_monitor, _, regular_user = monitor_and_users

        with pytest.raises(AuthorizationError):
            auth_monitor.get_observations(user=regular_user)

        with pytest.raises(AuthorizationError):
            auth_monitor.get_summary(user=regular_user)

        with pytest.raises(AuthorizationError):
            auth_monitor.verify_policy(user=regular_user)

        with pytest.raises(AuthorizationError):
            auth_monitor.collect_now(user=regular_user)
