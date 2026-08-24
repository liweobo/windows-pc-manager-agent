"""Fresh dual-inventory verification for controlled winget removal."""

from __future__ import annotations

from pc_manager_agent.domain.software_uninstall_analysis import NormalizedInstalledSoftware
from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    WingetProcessExecutionResult,
    WingetProcessResultCategory,
    WingetUninstallVerification,
    WingetVerificationState,
)
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.winget_target_resolver import PackageTargetResolver
from pc_manager_agent.tools.manifest import CancellationToken


class WingetUninstallVerifier:
    """Treat process exit as evidence and require both inventories for verified removal."""

    def __init__(
        self,
        package_resolver: PackageTargetResolver,
        software_resolver: SoftwareTargetResolver,
    ) -> None:
        self._package_resolver = package_resolver
        self._software_resolver = software_resolver

    def verify(
        self,
        package: NormalizedWingetPackage,
        software: NormalizedInstalledSoftware,
        process: WingetProcessExecutionResult,
        max_items: int,
        cancellation: CancellationToken | None = None,
    ) -> WingetUninstallVerification:
        """Refresh package and software identities and report every contradiction."""
        if process.category is WingetProcessResultCategory.MONITORING_STOPPED:
            return WingetUninstallVerification(
                state=WingetVerificationState.INTERRUPTED,
                package_inventory_refreshed=False,
                software_inventory_refreshed=False,
                original_package_present=None,
                original_software_present=None,
                evidence=("Monitoring stopped while winget or its installer may still run.",),
                warnings=("The Agent did not terminate or retry any process.",),
            )
        token = cancellation or CancellationToken()
        package_present: bool | None = None
        software_present: bool | None = None
        package_complete = False
        software_complete = False
        evidence: list[str] = []
        warnings: list[str] = []
        try:
            observed_package, inventory = self._package_resolver.inspect(
                package.identity.canonical_digest(),
                max_items,
                token,
            )
            package_present = observed_package is not None
            package_complete = inventory.state.value == "complete" and not inventory.warnings
            evidence.append("Fresh official-source package inventory was collected.")
            if not package_complete:
                warnings.append("Package inventory was partial; package absence is not proof.")
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Fresh package inventory failed: {type(exc).__name__}.")
        try:
            observed_software, snapshot = self._software_resolver.inspect(
                software.identity.canonical_digest(),
                max_items,
                token,
            )
            software_present = observed_software is not None
            software_complete = not snapshot.inventory.truncated and not snapshot.inventory.warnings
            evidence.append("Fresh installed-software inventory was collected.")
            if not software_complete:
                warnings.append("Software inventory was partial; absence is not proof.")
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Fresh software inventory failed: {type(exc).__name__}.")
        if package_complete and software_complete:
            if package_present is False and software_present is False:
                state = WingetVerificationState.VERIFIED_REMOVED
            elif package_present is False and software_present is True:
                state = WingetVerificationState.PACKAGE_REMOVED_SOFTWARE_PRESENT
            elif package_present is True:
                state = WingetVerificationState.PACKAGE_STILL_PRESENT
            else:
                state = WingetVerificationState.COMPLETED_UNVERIFIED
        elif software_complete and software_present is False and not package_complete:
            state = WingetVerificationState.SOFTWARE_REMOVED_PACKAGE_UNKNOWN
        else:
            state = WingetVerificationState.COMPLETED_UNVERIFIED
        return WingetUninstallVerification(
            state=state,
            package_inventory_refreshed=package_complete,
            software_inventory_refreshed=software_complete,
            original_package_present=package_present,
            original_software_present=software_present,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        )
