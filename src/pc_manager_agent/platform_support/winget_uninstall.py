"""Platform boundaries for trusted winget discovery, inventory, and execution."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.winget_uninstall import (
    ValidatedWingetUninstallAction,
    WingetAvailability,
    WingetPackageInventory,
    WingetProcessExecutionResult,
)
from pc_manager_agent.tools.manifest import CancellationToken


class WingetAvailabilityPlatform(Protocol):
    """Discover the exact trusted App Installer alias without PATH search."""

    def inspect(self) -> WingetAvailability:
        """Return a fail-closed executable identity observation."""
        ...


class WingetPackageInventoryPlatform(Protocol):
    """Collect a bounded official-source package inventory."""

    def inventory(
        self,
        max_items: int,
        cancellation: CancellationToken,
    ) -> WingetPackageInventory:
        """Return only structured package records parsed from trusted JSON."""
        ...


class WingetUninstallPlatform(Protocol):
    """Launch the fixed winget uninstall operation without shell or elevation."""

    def uninstall(
        self,
        action: ValidatedWingetUninstallAction,
        cancellation: CancellationToken,
    ) -> WingetProcessExecutionResult:
        """Launch once and monitor without terminating winget or its installer."""
        ...
