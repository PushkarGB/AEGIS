"""Replaceable network observation collectors.

Provides:
- NetworkCollector: Abstract base collector interface.
- InMemoryNetworkCollector: Replaceable collector for synthetic tests and controlled feeds.
- LocalConnectionCollector: Lightweight local prototype collector inspecting host sockets.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
import re
import subprocess
import sys
from typing import Sequence

from .network_classifier import classify_destination, determine_traffic_direction
from .network_models import (
    NetworkClassification,
    NetworkObservation,
    TrafficDirection,
    TrafficStatus,
)


class NetworkCollector(ABC):
    """Abstract collector interface for obtaining network observations.

    Implementations can be swapped between lightweight local prototype collectors,
    synthetic test feeders, eBPF probes, or host firewall log readers.
    """

    @abstractmethod
    def collect(self) -> list[NetworkObservation]:
        """Collect and return current network observations."""


class InMemoryNetworkCollector(NetworkCollector):
    """Replaceable in-memory collector for synthetic feeds and testing."""

    def __init__(self, initial_observations: Sequence[NetworkObservation] | None = None) -> None:
        self._observations: list[NetworkObservation] = list(initial_observations or [])

    def add_observation(self, observation: NetworkObservation) -> None:
        """Add an observation to the collector feed."""
        self._observations.append(observation)

    def add_synthetic(
        self,
        source: str,
        destination: str,
        destination_port: int | None = None,
        protocol: str = "TCP",
        direction: TrafficDirection | None = None,
        classification: NetworkClassification | None = None,
        process: str | None = None,
        container: str | None = None,
        status: TrafficStatus = TrafficStatus.OBSERVED,
        metadata: dict | None = None,
    ) -> NetworkObservation:
        """Convenience method to construct and append a synthetic observation."""
        inferred_class = classification or classify_destination(
            destination=destination,
            source=source,
            status=status,
        )
        inferred_dir = direction or determine_traffic_direction(
            source=source,
            destination=destination,
        )

        obs = NetworkObservation(
            source=source,
            destination=destination,
            destination_port=destination_port,
            protocol=protocol,
            direction=inferred_dir,
            classification=inferred_class,
            process=process,
            container=container,
            status=status,
            metadata=metadata or {},
        )
        self._observations.append(obs)
        return obs

    def clear(self) -> None:
        """Clear all stored observations."""
        self._observations.clear()

    def collect(self) -> list[NetworkObservation]:
        """Return all queued observations."""
        return list(self._observations)


class LocalConnectionCollector(NetworkCollector):
    """Lightweight local prototype collector appropriate for the development environment.

    Inspects active network connections on the local machine using psutil if
    available, with fallback to standard operating system utilities (netstat on
    Windows, ss/netstat on Linux).

    Strict Invariants:
    1. Replaceable: Implements the same NetworkCollector contract as synthetic/eBPF collectors.
    2. Zero External Telemetry: Operates completely offline without sending any data.
    3. Zero Fabricated Blocks: Only actually observed connections are reported.
    """

    def __init__(
        self,
        internal_cidrs: Sequence[str] | None = None,
        include_loopback: bool = True,
    ) -> None:
        self._internal_cidrs = internal_cidrs
        self._include_loopback = include_loopback

    def collect(self) -> list[NetworkObservation]:
        """Collect active connections from the local operating system."""
        # Try psutil first if installed
        try:
            return self._collect_psutil()
        except ImportError:
            pass
        except Exception:
            # Fall back to OS utilities if psutil fails
            pass

        # Fallback to standard OS utilities
        if sys.platform == "win32":
            return self._collect_windows_netstat()
        return self._collect_unix_netstat()

    def _collect_psutil(self) -> list[NetworkObservation]:
        import psutil  # type: ignore[import-not-found]

        observations: list[NetworkObservation] = []
        connections = psutil.net_connections(kind="inet")

        for conn in connections:
            if not conn.raddr:
                # Listening socket or unbound endpoint without remote peer
                continue

            src_ip = conn.laddr.ip if conn.laddr else "127.0.0.1"
            dst_ip = conn.raddr.ip
            dst_port = conn.raddr.port

            # Check loopback filtering
            if not self._include_loopback and (
                dst_ip.startswith("127.") or dst_ip == "::1" or src_ip.startswith("127.")
            ):
                continue

            # Resolve process name safely
            proc_name = None
            if conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    proc_name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc_name = f"pid:{conn.pid}"

            protocol = "TCP" if conn.type == 1 else "UDP"
            status = (
                TrafficStatus.ALLOWED
                if conn.status == "ESTABLISHED"
                else TrafficStatus.OBSERVED
            )

            classification = classify_destination(
                destination=dst_ip,
                source=src_ip,
                status=status,
                internal_cidrs=self._internal_cidrs,
            )
            direction = determine_traffic_direction(
                source=src_ip,
                destination=dst_ip,
                internal_cidrs=self._internal_cidrs,
            )

            observations.append(
                NetworkObservation(
                    source=src_ip,
                    destination=dst_ip,
                    destination_port=dst_port,
                    protocol=protocol,
                    direction=direction,
                    classification=classification,
                    process=proc_name,
                    status=status,
                    metadata={"connection_status": str(conn.status), "pid": conn.pid},
                )
            )

        return observations

    def _collect_windows_netstat(self) -> list[NetworkObservation]:
        """Parse active connections on Windows using 'netstat -ano'."""
        observations: list[NetworkObservation] = []
        try:
            res = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0 or not res.stdout:
                return observations

            lines = res.stdout.splitlines()
            for line in lines:
                parts = line.split()
                if len(parts) < 4:
                    continue

                proto = parts[0].upper()
                if proto not in ("TCP", "UDP"):
                    continue

                local_addr = parts[1]
                foreign_addr = parts[2]
                state = parts[3] if proto == "TCP" and len(parts) >= 5 else "OBSERVED"
                pid_str = parts[-1] if parts[-1].isdigit() else None

                # Ignore non-connected/listening sockets without foreign host
                if foreign_addr in ("*:*", "0.0.0.0:0", "[::]:0"):
                    continue

                # Parse host and port
                src_host, _ = self._split_host_port(local_addr)
                dst_host, dst_port = self._split_host_port(foreign_addr)

                if not dst_host:
                    continue

                if not self._include_loopback and (
                    dst_host.startswith("127.") or dst_host == "::1"
                ):
                    continue

                status = (
                    TrafficStatus.ALLOWED
                    if state.upper() == "ESTABLISHED"
                    else TrafficStatus.OBSERVED
                )

                classification = classify_destination(
                    destination=dst_host,
                    source=src_host,
                    status=status,
                    internal_cidrs=self._internal_cidrs,
                )
                direction = determine_traffic_direction(
                    source=src_host,
                    destination=dst_host,
                    internal_cidrs=self._internal_cidrs,
                )

                observations.append(
                    NetworkObservation(
                        source=src_host or "127.0.0.1",
                        destination=dst_host,
                        destination_port=dst_port,
                        protocol=proto,
                        direction=direction,
                        classification=classification,
                        process=f"pid:{pid_str}" if pid_str else None,
                        status=status,
                        metadata={"state": state, "pid": int(pid_str) if pid_str else None},
                    )
                )
        except Exception:
            # Prototype collector fails gracefully without breaking caller
            pass

        return observations

    def _collect_unix_netstat(self) -> list[NetworkObservation]:
        """Parse active connections on Linux/Unix using 'ss -tuanp' or 'netstat -tuan'."""
        observations: list[NetworkObservation] = []
        try:
            # Try ss first
            cmd = ["ss", "-tuan"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            if res.returncode != 0:
                cmd = ["netstat", "-tuan"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)

            if res.returncode != 0 or not res.stdout:
                return observations

            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].lower() in ("state", "proto", "netid"):
                    continue

                proto = parts[0].upper()
                if "TCP" in proto:
                    proto = "TCP"
                elif "UDP" in proto:
                    proto = "UDP"
                else:
                    continue

                # ss format vs netstat format
                local_addr = parts[4] if len(parts) > 5 else parts[3]
                foreign_addr = parts[5] if len(parts) > 5 else parts[4]

                src_host, _ = self._split_host_port(local_addr)
                dst_host, dst_port = self._split_host_port(foreign_addr)

                if not dst_host or dst_host in ("*", "0.0.0.0", "::"):
                    continue

                classification = classify_destination(
                    destination=dst_host,
                    source=src_host,
                    status=TrafficStatus.OBSERVED,
                    internal_cidrs=self._internal_cidrs,
                )
                direction = determine_traffic_direction(
                    source=src_host,
                    destination=dst_host,
                    internal_cidrs=self._internal_cidrs,
                )

                observations.append(
                    NetworkObservation(
                        source=src_host or "127.0.0.1",
                        destination=dst_host,
                        destination_port=dst_port,
                        protocol=proto,
                        direction=direction,
                        classification=classification,
                        status=TrafficStatus.OBSERVED,
                        metadata={"raw": line.strip()},
                    )
                )
        except Exception:
            pass

        return observations

    @staticmethod
    def _split_host_port(addr_str: str) -> tuple[str, int | None]:
        """Split a host:port string handling IPv4 and IPv6 bracketed syntax."""
        addr = addr_str.strip()
        if not addr:
            return "", None

        if addr.startswith("[") and "]:" in addr:
            # Bracketed IPv6 e.g. [::1]:8080
            idx = addr.index("]:")
            host = addr[1:idx]
            port_str = addr[idx + 2 :]
            try:
                return host, int(port_str)
            except ValueError:
                return host, None

        if ":" in addr:
            # Check if IPv6 without brackets or IPv4 with port
            if addr.count(":") == 1:
                parts = addr.split(":")
                try:
                    return parts[0], int(parts[1])
                except ValueError:
                    return parts[0], None
            # IPv6 with multiple colons
            return addr, None

        return addr, None
