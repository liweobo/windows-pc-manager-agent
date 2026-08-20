"""Cross-platform contracts for read-only installed-software metadata."""

from typing import Protocol

from pc_manager_agent.domain.software_uninstall_analysis import RawInstalledSoftwareEntry
from pc_manager_agent.platform_support.base import CancellationSignal


class PackageInventoryProvider(Protocol):
    """Optional structured current-user package source such as a future MSIX adapter."""

    def collect(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Return bounded package records without installing, removing, or updating packages."""
        ...


class SoftwareInventoryPlatform(Protocol):
    """Read-only platform adapter for raw installed-software metadata."""

    def collect_raw(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Collect bounded untrusted source records without invoking uninstall metadata."""
        ...
