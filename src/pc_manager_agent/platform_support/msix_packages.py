"""Platform-neutral interfaces for current-user MSIX discovery and removal."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.msix_uninstall import (
    MsixDependencySnapshot,
    MsixDeploymentResult,
    MsixPackageIdentity,
    MsixPackageInventory,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.tools.manifest import CancellationToken


class MsixPackagePlatform(Protocol):
    """Narrow platform contract that never exposes all-user or provisioned removal."""

    def inventory_current_user(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> MsixPackageInventory:
        """Return a bounded current-user package inventory."""
        ...

    def remove_current_user(
        self,
        action: ValidatedMsixRemovalAction,
        cancellation: CancellationToken,
    ) -> MsixDeploymentResult:
        """Remove exactly one validated current-user package instance."""
        ...

    def dependency_snapshot(self, identity: MsixPackageIdentity) -> MsixDependencySnapshot:
        """Return bounded direct and reverse current-user relationship evidence."""
        ...
