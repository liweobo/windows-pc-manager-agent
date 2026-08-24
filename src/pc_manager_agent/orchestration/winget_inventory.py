"""Read-only winget availability and package inventory services."""

from __future__ import annotations

from pc_manager_agent.domain.winget_uninstall import (
    WingetAvailability,
    WingetPackageInventory,
)
from pc_manager_agent.platform_support.winget_uninstall import (
    WingetAvailabilityPlatform,
    WingetPackageInventoryPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


class WingetAvailabilityService:
    """Expose trusted alias discovery without any execution authority."""

    def __init__(self, platform: WingetAvailabilityPlatform) -> None:
        self._platform = platform

    def inspect(self) -> WingetAvailability:
        """Return the current exact App Installer alias observation."""
        return self._platform.inspect()


class PackageInventoryService:
    """Collect bounded structured package data from the official winget source."""

    def __init__(self, platform: WingetPackageInventoryPlatform) -> None:
        self._platform = platform

    def inventory(
        self,
        max_items: int,
        cancellation: CancellationToken | None = None,
    ) -> WingetPackageInventory:
        """Return an inventory or explicit partial/failure state."""
        return self._platform.inventory(max_items, cancellation or CancellationToken())
