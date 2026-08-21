"""Fresh inventory verification for Vendor uninstall process outcomes."""

from __future__ import annotations

from pc_manager_agent.domain.software_uninstall_analysis import NormalizedInstalledSoftware
from pc_manager_agent.domain.vendor_uninstall import (
    VendorProcessExecutionResult,
    VendorProcessResultCategory,
    VendorUninstallVerification,
    VendorVerificationState,
)
from pc_manager_agent.orchestration.software_inventory import SoftwareInventorySnapshot
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.tools.manifest import CancellationToken


class VendorUninstallVerifier:
    """Classify fresh inventory independently from the Vendor process exit code."""

    def __init__(self, resolver: SoftwareTargetResolver) -> None:
        self._resolver = resolver

    def verify(
        self,
        target: NormalizedInstalledSoftware,
        process: VendorProcessExecutionResult,
        max_items: int,
        cancellation: CancellationToken,
    ) -> VendorUninstallVerification:
        """Refresh the exact identity and describe contradictory evidence truthfully."""
        warnings: list[str] = []
        evidence: list[str] = []
        identity_present: bool | None = None
        replacement_count = 0
        complete = False
        try:
            software, snapshot = self._resolver.inspect(
                target.identity.canonical_digest(),
                max_items,
                cancellation,
            )
            identity_present = software is not None
            replacement_count = _replacement_count(snapshot, target)
            complete = not snapshot.inventory.truncated and not snapshot.inventory.warnings
            evidence.append("Fresh uninstall-registry inventory was collected after process exit.")
            if not complete:
                warnings.append("Fresh inventory was partial; absence cannot prove removal.")
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Fresh software inventory failed: {type(exc).__name__}.")
        exited_zero = process.category is VendorProcessResultCategory.PROCESS_EXITED_ZERO
        exited_nonzero = process.category is VendorProcessResultCategory.PROCESS_EXITED_NONZERO
        if replacement_count:
            state = VendorVerificationState.TARGET_INSTANCE_CHANGED
        elif complete and identity_present is False:
            state = (
                VendorVerificationState.VERIFIED_REMOVED
                if exited_zero
                else VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT
            )
        elif complete and identity_present is True and (exited_zero or exited_nonzero):
            state = VendorVerificationState.FAILED
        else:
            state = VendorVerificationState.COMPLETED_UNVERIFIED
        return VendorUninstallVerification(
            state=state,
            original_identity_present=identity_present,
            replacement_candidates=replacement_count,
            inventory_refreshed=complete,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        )


def _replacement_count(
    snapshot: SoftwareInventorySnapshot,
    target: NormalizedInstalledSoftware,
) -> int:
    """Count same-name/publisher/scope entries with a different source identity."""
    return sum(
        1
        for item in snapshot.inventory.entries
        if item.identity.canonical_digest() != target.identity.canonical_digest()
        and _same(item.display_name, target.display_name)
        and _same(item.publisher, target.publisher)
        and item.scope is target.scope
    )


def _same(left: str | None, right: str | None) -> bool:
    """Compare normalized visible identity text without fuzzy matching."""
    if left is None or right is None:
        return False
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
