"""Platform-neutral boundary for exact service inspection and control."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceIdentity,
    ServiceObservation,
    ServicePermissionEvidence,
    ServiceState,
    ServiceStepResult,
)
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceControlPlatform(Protocol):
    """Query and control only exact local Windows service identities."""

    def list_services(self, max_items: int = 5_000) -> tuple[ServiceObservation, ...]:
        """Return a bounded fresh service inventory without requesting control access."""
        ...

    def inspect(self, service_name: str) -> ServiceObservation | None:
        """Read exact current configuration, state, dependencies, and dependents."""
        ...

    def evaluate_permissions(
        self,
        service_name: str,
        action: ServiceActionType,
    ) -> ServicePermissionEvidence:
        """Probe action-specific handle rights without sending a control code."""
        ...

    def start(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Start and verify one exact service, without recursively starting dependencies."""
        ...

    def stop(
        self,
        identity: ServiceIdentity,
        expected_state: ServiceState,
        timeout_seconds: float,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Stop and verify one exact service, without cascading to dependents."""
        ...
