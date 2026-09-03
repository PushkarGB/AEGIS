"""Deterministic network traffic classification for sovereign observability.

This module provides offline, zero-telemetry classification of network endpoints.
All classifications are computed purely locally without performing live DNS
queries or contacting external network services.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Sequence

from .network_models import (
    NetworkClassification,
    TrafficDirection,
    TrafficStatus,
)

# Standard local / internal hostname patterns
_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}

_INTERNAL_SUFFIXES = (
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".test",
    ".example",
    ".invalid",
)

# Known external top-level domains for offline heuristic matching without external DNS
_EXTERNAL_TLD_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|edu|gov|io|ai|dev|co|app|cloud|info|biz|[a-z]{2})$",
    re.IGNORECASE,
)


def is_internal_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    extra_internal_cidrs: Sequence[str] | None = None,
) -> bool:
    """Determine whether an IP address belongs to internal / private / loopback spaces."""
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True

    if extra_internal_cidrs:
        for cidr_str in extra_internal_cidrs:
            try:
                network = ipaddress.ip_network(cidr_str, strict=False)
                if ip in network:
                    return True
            except ValueError:
                continue

    return False


def classify_destination(
    destination: str,
    source: str | None = None,
    status: TrafficStatus = TrafficStatus.OBSERVED,
    internal_cidrs: Sequence[str] | None = None,
) -> NetworkClassification:
    """Classify a network destination into INTERNAL, EXTERNAL, BLOCKED, or UNKNOWN.

    Strict Invariants:
    1. Zero External Telemetry: No external DNS or socket queries are initiated.
    2. Zero Fabricated Blocks: BLOCKED classification is returned only if the
       observed status was actually BLOCKED.
    """
    # 1. Actually observed blocked/dropped traffic
    if status == TrafficStatus.BLOCKED:
        return NetworkClassification.BLOCKED

    dest = destination.strip()
    if not dest:
        return NetworkClassification.UNKNOWN

    # Strip port if present in string like "127.0.0.1:8000" or "[::1]:8080"
    if dest.startswith("[") and "]" in dest:
        dest = dest[1 : dest.index("]")]
    elif ":" in dest and dest.count(":") == 1:
        # IPv4 with port e.g. "192.168.1.1:443"
        dest = dest.split(":", 1)[0]

    # Check hostname patterns first
    dest_lower = dest.lower()
    if dest_lower in _LOCAL_HOSTNAMES or any(
        dest_lower.endswith(suffix) for suffix in _INTERNAL_SUFFIXES
    ):
        return NetworkClassification.INTERNAL

    # Try IP address parsing
    try:
        ip = ipaddress.ip_address(dest)
        if is_internal_ip(ip, internal_cidrs):
            return NetworkClassification.INTERNAL
        if ip.is_global:
            return NetworkClassification.EXTERNAL
        # Reserved or indeterminate IP
        return NetworkClassification.UNKNOWN
    except ValueError:
        pass

    # Domain name heuristic (offline, no DNS query)
    if _EXTERNAL_TLD_PATTERN.match(dest_lower):
        return NetworkClassification.EXTERNAL

    return NetworkClassification.UNKNOWN


def determine_traffic_direction(
    source: str | None,
    destination: str | None,
    internal_cidrs: Sequence[str] | None = None,
) -> TrafficDirection:
    """Determine the direction of traffic based on source and destination."""
    if not source or not destination:
        return TrafficDirection.UNKNOWN

    src_class = classify_destination(source, internal_cidrs=internal_cidrs)
    dst_class = classify_destination(destination, internal_cidrs=internal_cidrs)

    # If both endpoints are internal or loopback
    if src_class == NetworkClassification.INTERNAL and dst_class == NetworkClassification.INTERNAL:
        # Check if loopback specifically
        try:
            s_ip = ipaddress.ip_address(source.split(":")[0].strip("[]"))
            d_ip = ipaddress.ip_address(destination.split(":")[0].strip("[]"))
            if s_ip.is_loopback or d_ip.is_loopback:
                return TrafficDirection.LOOPBACK
        except ValueError:
            if source.lower() in _LOCAL_HOSTNAMES or destination.lower() in _LOCAL_HOSTNAMES:
                return TrafficDirection.LOOPBACK
        return TrafficDirection.LOOPBACK

    if src_class == NetworkClassification.INTERNAL and dst_class == NetworkClassification.EXTERNAL:
        return TrafficDirection.OUTBOUND

    if src_class == NetworkClassification.EXTERNAL and dst_class == NetworkClassification.INTERNAL:
        return TrafficDirection.INBOUND

    if dst_class == NetworkClassification.EXTERNAL:
        return TrafficDirection.OUTBOUND

    if src_class == NetworkClassification.EXTERNAL:
        return TrafficDirection.INBOUND

    return TrafficDirection.UNKNOWN
