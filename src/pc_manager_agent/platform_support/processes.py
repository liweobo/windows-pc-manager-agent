"""Platform-neutral protocols for Stage 4A process inspection and control."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.process_actions import (
    ProcessIdentity,
    ProcessMemberResult,
    ProcessObservation,
)
from pc_manager_agent.platform_support.base import CancellationSignal


class ProcessManagementPlatform(Protocol):
    """Narrow operating-system boundary; no shell or arbitrary command is exposed."""

    def list_processes(self, max_processes: int) -> tuple[ProcessObservation, ...]:
        """Return fresh, identity-complete observations within a strict bound."""
        ...

    def inspect_process(self, pid: int) -> ProcessObservation | None:
        """Return a fresh observation, or ``None`` when the PID no longer exists."""
        ...

    def request_graceful_exit(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
        cancellation: CancellationSignal,
    ) -> ProcessMemberResult:
        """Revalidate one identity, post WM_CLOSE, wait, and report verified state."""
        ...

    def force_terminate(
        self,
        identity: ProcessIdentity,
        timeout_seconds: float,
    ) -> ProcessMemberResult:
        """Revalidate one identity on its process handle, terminate, wait, and verify."""
        ...
