"""Platform-neutral launch and byte-stream boundaries for an elevated Broker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pc_manager_agent.domain.elevated_broker import BrokerBinaryIdentity
from pc_manager_agent.privileged.ipc_protocol import PipeByteStream


class ElevationLaunchStatus(StrEnum):
    """Deterministic outcomes of the explicit operating-system elevation prompt."""

    STARTED = "STARTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class BrokerLaunchArguments:
    """Only opaque routing identifiers permitted on the Broker command line."""

    broker_instance_id: UUID
    rendezvous_id: str
    protocol_version: int
    expected_caller_process_id: int
    agent_instance_id: UUID


@dataclass(frozen=True, slots=True)
class ElevatedProcessHandle:
    """Owned process correlation handle returned by the Windows launcher."""

    process_id: int
    native_handle: object


@dataclass(frozen=True, slots=True)
class ElevationLaunchResult:
    """UAC launch conclusion without credentials or Shell command text."""

    status: ElevationLaunchStatus
    process: ElevatedProcessHandle | None = None
    error_code: int | None = None


class BrokerBinaryInspector(Protocol):
    """Read and evaluate the exact Broker executable before requesting UAC."""

    def inspect(self, path: Path) -> BrokerBinaryIdentity:
        """Return stable file, signature, manifest, and location evidence."""
        ...


class ElevatedBrokerLauncher(Protocol):
    """Launch one fixed Broker through the operating-system UAC surface."""

    def launch(
        self,
        broker_path: Path,
        arguments: BrokerLaunchArguments,
    ) -> ElevationLaunchResult:
        """Request elevation without accepting executable or argument overrides."""
        ...

    def wait_for_exit(
        self,
        process: ElevatedProcessHandle,
        *,
        timeout_seconds: float,
    ) -> int | None:
        """Return the process exit code or ``None`` without terminating on timeout."""
        ...

    def close_process_handle(self, process: ElevatedProcessHandle) -> None:
        """Close the owned correlation handle without terminating the Broker."""
        ...


class BrokerPipeServer(Protocol):
    """One-client local IPC endpoint created by the elevated Broker."""

    def accept(self, *, timeout_seconds: float) -> BrokerPipeConnection:
        """Accept exactly one bounded local client."""
        ...

    def close(self) -> None:
        """Close the endpoint and unblock any pending operation."""
        ...


class BrokerPipeClient(Protocol):
    """Standard-user connection to one exact Broker-created endpoint."""

    def connect(self, *, timeout_seconds: float) -> BrokerPipeConnection:
        """Connect to one local endpoint or fail at the deadline."""
        ...


class BrokerPipeConnection(PipeByteStream, Protocol):
    """Connected local pipe plus OS peer identity fields and owned close."""

    @property
    def native_handle(self) -> int:
        """Return the native handle only for reviewed Windows identity APIs."""
        ...

    @property
    def peer_process_id(self) -> int:
        """Return the peer PID supplied by the named-pipe filesystem."""
        ...

    @property
    def peer_session_id(self) -> int:
        """Return the peer Windows session supplied by the pipe filesystem."""
        ...

    def close(self) -> None:
        """Close the connected handle exactly once."""
        ...
