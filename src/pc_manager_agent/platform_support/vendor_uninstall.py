"""Platform boundaries for Vendor executable inspection and controlled launch."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.vendor_uninstall import (
    ValidatedVendorUninstallAction,
    VendorExecutableObservation,
    VendorProcessExecutionResult,
)
from pc_manager_agent.tools.manifest import CancellationToken


class VendorExecutablePlatform(Protocol):
    """Inspect one explicit local executable without launching it."""

    def inspect(
        self, executable: Path, install_location: Path, publisher: str
    ) -> VendorExecutableObservation:
        """Return handle-backed identity, hash, signature, and path evidence."""
        ...


class VendorUninstallPlatform(Protocol):
    """Launch only a fully validated Vendor action without a shell or elevation."""

    def uninstall(
        self,
        action: ValidatedVendorUninstallAction,
        cancellation: CancellationToken,
    ) -> VendorProcessExecutionResult:
        """Launch once, monitor without process control, and return process evidence."""
        ...
