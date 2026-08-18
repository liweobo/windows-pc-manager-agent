"""Platform-neutral boundary for narrow service startup configuration changes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupMutationResult,
    ServiceStartupPermissionEvidence,
)
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceStartupPlatform(Protocol):
    """Expose only Automatic, Manual, and verified-restore configuration primitives."""

    def evaluate_permissions(self, service_name: str) -> ServiceStartupPermissionEvidence:
        """Probe query/change-config handle access without changing service state."""
        ...

    def set_automatic(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Set one exact service to non-delayed Automatic and verify by reading SCM."""
        ...

    def set_manual(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Set one exact service to Manual and verify by reading SCM."""
        ...

    def restore(
        self,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStartupMutationResult:
        """Restore one exact Automatic/Manual configuration after conflict checks."""
        ...
