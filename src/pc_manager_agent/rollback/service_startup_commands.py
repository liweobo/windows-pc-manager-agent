"""Command object for verified service startup writes and separately authorized restore."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.service_actions import ServiceStartupConfiguration
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupMutationResult,
)
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceStartupUndoRecord(FrozenModel):
    """Reference needed to prepare, confirm, and conflict-check a later restore."""

    backup_id: UUID
    backup_digest: str
    agent_written_configuration: ServiceStartupConfiguration
    restore_configuration: ServiceStartupConfiguration
    rollback_level: RollbackLevel = RollbackLevel.FULL
    requires_fresh_confirmation: bool = True


class ServiceStartupConfigurationCommand:
    """Execute one narrow tool request and expose truthful verification/undo metadata."""

    def __init__(
        self,
        platform: ServiceStartupPlatform,
        request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
    ) -> None:
        self._platform = platform
        self._request = request
        self._cancellation = cancellation
        self._result: ServiceStartupMutationResult | None = None

    def execute(self) -> ServiceStartupMutationResult:
        """Dispatch exactly the action encoded by the registered tool request."""
        if self._request.action is ServiceStartupActionType.SET_AUTOMATIC:
            result = self._platform.set_automatic(self._request, self._cancellation)
        elif self._request.action is ServiceStartupActionType.SET_MANUAL:
            result = self._platform.set_manual(self._request, self._cancellation)
        else:
            result = self._platform.restore(self._request, self._cancellation)
        self._result = result
        return result

    def verify(self) -> bool:
        """Return whether SCM read-back and runtime-state invariants both passed."""
        return bool(
            self._result is not None and self._result.verified and self._result.runtime_unchanged
        )

    def build_undo_record(self) -> ServiceStartupUndoRecord:
        """Build a non-secret reference; it never performs an automatic reverse write."""
        if not self.verify():
            raise RuntimeError("Cannot build undo metadata for an unverified write")
        return ServiceStartupUndoRecord(
            backup_id=self._request.backup_id,
            backup_digest=self._request.backup_digest,
            agent_written_configuration=self._request.target_configuration,
            restore_configuration=self._request.expected_source_configuration,
        )

    def rollback(
        self,
        confirmed_restore_request: ServiceStartupActionRequest,
        cancellation: CancellationToken,
    ) -> ServiceStartupMutationResult:
        """Execute only an independently confirmed exact reverse request via restore adapter."""
        if confirmed_restore_request.action is not ServiceStartupActionType.RESTORE:
            raise ValueError("Rollback requires an independently authorized RESTORE request")
        if (
            confirmed_restore_request.expected_source_configuration
            != self._request.target_configuration
            or confirmed_restore_request.target_configuration
            != self._request.expected_source_configuration
        ):
            raise ValueError("Restore request is not the exact reverse transition")
        return self._platform.restore(confirmed_restore_request, cancellation)
