"""Sandbox, sovereignty controls, and network monitoring abstraction.

Generated code must not run on the host. Network activity is observed and
classified locally to enforce sovereignty and zero-egress invariants.
"""

from .collector import (
    InMemoryNetworkCollector,
    LocalConnectionCollector,
    NetworkCollector,
)
from .network_classifier import (
    classify_destination,
    determine_traffic_direction,
    is_internal_ip,
)
from .network_models import (
    NetworkClassification,
    NetworkObservation,
    NetworkPolicy,
    NetworkSummary,
    PolicyViolation,
    TrafficDirection,
    TrafficStatus,
)
from .network_monitor import (
    AuthorizedNetworkMonitor,
    NetworkMonitor,
    StandardNetworkMonitor,
)

__all__ = [
    # Models and Enums
    "NetworkClassification",
    "TrafficDirection",
    "TrafficStatus",
    "NetworkObservation",
    "NetworkPolicy",
    "PolicyViolation",
    "NetworkSummary",
    # Classifier functions
    "classify_destination",
    "determine_traffic_direction",
    "is_internal_ip",
    # Collectors
    "NetworkCollector",
    "InMemoryNetworkCollector",
    "LocalConnectionCollector",
    # Monitors
    "NetworkMonitor",
    "StandardNetworkMonitor",
    "AuthorizedNetworkMonitor",
]
