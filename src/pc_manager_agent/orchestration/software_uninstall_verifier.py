"""Post-uninstall verification that never equates an exit code with final success."""

from __future__ import annotations

from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallerExecutionResult,
    MsiInstallerResultCategory,
    MsiUninstallVerification,
    MsiVerificationState,
    ValidatedMsiProduct,
)
from pc_manager_agent.orchestration.software_inventory import SoftwareInventorySnapshot
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.platform_support.msi_uninstall import MsiProductInventoryPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class MsiUninstallVerifier:
    """Combine fresh normalized inventory and Windows Installer registration evidence."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        msi_inventory: MsiProductInventoryPlatform,
    ) -> None:
        self._resolver = resolver
        self._msi_inventory = msi_inventory

    def verify(
        self,
        product: ValidatedMsiProduct,
        installer: MsiInstallerExecutionResult,
        max_items: int,
        cancellation: CancellationToken,
    ) -> MsiUninstallVerification:
        """Refresh both inventories and classify contradictions explicitly."""
        warnings: list[str] = []
        evidence: list[str] = []
        identity_present: bool | None = None
        product_present: bool | None = None
        replacement_count = 0
        snapshot: SoftwareInventorySnapshot | None = None
        registry_inventory_complete = False
        try:
            software, snapshot = self._resolver.inspect(
                product.identity_digest,
                max_items,
                cancellation,
            )
            identity_present = software is not None
            replacement_count = _replacement_count(snapshot, product)
            registry_inventory_complete = (
                not snapshot.inventory.truncated and not snapshot.inventory.warnings
            )
            if not registry_inventory_complete:
                warnings.append(
                    "Fresh software inventory was partial; absence cannot prove removal."
                )
            evidence.append(
                "Fresh uninstall-registry inventory was collected after installer completion."
            )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Fresh software inventory failed: {type(exc).__name__}.")
        try:
            registrations = self._msi_inventory.registrations(product.product_code)
            product_present = any(item.installed for item in registrations)
            evidence.append("Windows Installer registration was queried after completion.")
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Windows Installer verification failed: {type(exc).__name__}.")
        refreshed = registry_inventory_complete and product_present is not None
        successful_installer = installer.category in {
            MsiInstallerResultCategory.SUCCESS,
            MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED,
        }
        if replacement_count:
            state = MsiVerificationState.TARGET_REPLACED_OR_UPGRADED
        elif refreshed and identity_present is False and product_present is False:
            state = (
                MsiVerificationState.VERIFIED_REMOVED
                if successful_installer
                else MsiVerificationState.REMOVED_WITH_UNEXPECTED_INSTALLER_RESULT
            )
        elif not refreshed or successful_installer:
            state = MsiVerificationState.COMPLETED_UNVERIFIED
        else:
            state = MsiVerificationState.FAILED
        return MsiUninstallVerification(
            state=state,
            original_identity_present=identity_present,
            original_product_code_present=product_present,
            replacement_candidates=replacement_count,
            inventory_refreshed=refreshed,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        )


def _replacement_count(
    snapshot: SoftwareInventorySnapshot,
    product: ValidatedMsiProduct,
) -> int:
    return sum(
        1
        for item in snapshot.inventory.entries
        if item.identity.canonical_digest() != product.identity_digest
        and _same(item.display_name, product.display_name)
        and _same(item.publisher, product.publisher)
        and item.scope is product.scope
    )


def _same(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
