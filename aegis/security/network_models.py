"""Network monitoring models and contracts for sovereign network visibility.

This module defines provider-independent data structures for observing network
activity, classifying destinations, configuring network policy, and summarizing
traffic observations without leaking telemetry externally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NetworkClassification(StrEnum):
    """Classification of network observation destination.

    INTERNAL: Destination is local loopback, RFC 1918 private network, link-local,
              or local service endpoint.
    EXTERNAL: Destination is a routable public internet IP address or external host.
    BLOCKED:  Traffic that was actually observed to be dropped or blocked by the OS,
              firewall, or sandbox isolation (never fabricated).
    UNKNOWN:  Destination could not be parsed, is unresolvable without external DNS,
              or is malformed.
    """

    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class TrafficDirection(StrEnum):
    """Direction of the observed network traffic."""

    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    LOOPBACK = "LOOPBACK"
    UNKNOWN = "UNKNOWN"


class TrafficStatus(StrEnum):
    """Observable status of network traffic.

    ALLOWED:  Traffic was observed to be successfully transmitted or established.
    BLOCKED:  Traffic was observed to be rejected, dropped, or connection refused.
    OBSERVED: Traffic connection exists or was observed in active state.
    UNKNOWN:  Status could not be determined.
    """

    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    OBSERVED = "OBSERVED"
    UNKNOWN = "UNKNOWN"


class NetworkObservation(BaseModel):
    """An immutable, structured record of observed network activity.

    Observed network traffic represents actual sockets, packets, or connection
    states observed in the runtime. Blocked status is recorded ONLY when an
    actual blocked/dropped event is observed; it is never fabricated from policy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    observation_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utc_now)
    source: str = Field(min_length=1, description="Source IP, hostname, or interface")
    destination: str = Field(min_length=1, description="Destination IP or hostname")
    destination_port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str = Field(default="TCP", description="Network protocol (e.g. TCP, UDP, ICMP)")
    direction: TrafficDirection = Field(default=TrafficDirection.UNKNOWN)
    classification: NetworkClassification = Field(default=NetworkClassification.UNKNOWN)
    process: str | None = Field(default=None, description="Host process name or PID where available")
    container: str | None = Field(default=None, description="Container name or ID where available")
    status: TrafficStatus = Field(
        default=TrafficStatus.OBSERVED,
        description="Allowed or blocked status where observable",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe operational metadata without confidential task content",
    )

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Observation timestamp must be timezone-aware (UTC)")
        return value

    @property
    def process_or_container(self) -> str | None:
        """Convenience property returning container or process name where available."""
        if self.container:
            return self.container
        return self.process


class NetworkPolicy(BaseModel):
    """Configured network policy specification.

    Network policy is strictly separated from observed traffic. A policy specifies
    what is allowed or expected in the deployment, and allows verifying whether
    observed traffic complies with sovereignty requirements (such as zero external egress).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    policy_id: str = Field(default="sovereign-default", min_length=1)
    description: str = Field(default="Default sovereign on-premise network policy")
    allow_external: bool = Field(
        default=False,
        description="Whether external internet egress is permitted. Default False for strict sovereignty.",
    )
    allowed_internal_cidrs: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.0/8",      # IPv4 loopback
            "::1/128",          # IPv6 loopback
            "10.0.0.0/8",       # RFC 1918 class A
            "172.16.0.0/12",    # RFC 1918 class B (includes Docker 172.17.0.0/16)
            "192.168.0.0/16",   # RFC 1918 class C
            "169.254.0.0/16",   # IPv4 link-local
            "fe80::/10",        # IPv6 link-local
        ],
        description="Subnets considered internal",
    )
    allowed_destination_ports: list[int] | None = Field(
        default=None,
        description="Permitted destination ports if restricted, or None for all ports",
    )
    allowed_protocols: list[str] = Field(
        default_factory=lambda: ["TCP", "UDP"],
        description="Permitted transport protocols",
    )
    require_sandbox_isolation: bool = Field(
        default=True,
        description="If True, sandbox container traffic must not access external networks",
    )


class PolicyViolation(BaseModel):
    """Result of evaluating an observed network event against configured policy.

    This represents a compliance finding on observed traffic. It does NOT fabricate
    a network event; it assesses observed reality against policy expectations.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    violation_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utc_now)
    observation_id: UUID
    policy_id: str
    reason: str
    observation: NetworkObservation


class NetworkSummary(BaseModel):
    """Aggregated statistics of observed network traffic."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    total_observations: int = Field(default=0, ge=0)
    internal_count: int = Field(default=0, ge=0)
    external_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    inbound_count: int = Field(default=0, ge=0)
    outbound_count: int = Field(default=0, ge=0)
    loopback_count: int = Field(default=0, ge=0)
    policy_violations: int = Field(default=0, ge=0)
    first_observed: datetime | None = None
    last_observed: datetime | None = None
