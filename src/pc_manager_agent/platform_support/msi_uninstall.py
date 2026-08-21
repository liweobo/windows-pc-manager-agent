"""Narrow platform contracts for MSI registration inspection and uninstall execution."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallerExecutionResult,
    MsiProductRegistration,
    ValidatedMsiProduct,
)
from pc_manager_agent.tools.manifest import CancellationToken


class MsiProductInventoryPlatform(Protocol):
    """Read product registrations from Windows Installer without mutation."""

    def registrations(self, product_code: str) -> tuple[MsiProductRegistration, ...]:
        """Return all current-user and machine contexts for one canonical ProductCode."""
        ...


class MsiUninstallPlatform(Protocol):
    """Execute only a previously validated MSI product through a fixed adapter."""

    def uninstall(
        self,
        product: ValidatedMsiProduct,
        cancellation: CancellationToken,
    ) -> MsiInstallerExecutionResult:
        """Run the fixed interactive uninstall and never kill it after launch."""
        ...
