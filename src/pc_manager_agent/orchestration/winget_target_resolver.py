"""Exact, ambiguity-preserving target resolution for winget packages."""

from __future__ import annotations

from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    ResolvedWingetPackage,
    WingetInventoryState,
    WingetPackageInventory,
    WingetPackageQuery,
)
from pc_manager_agent.orchestration.winget_inventory import PackageInventoryService
from pc_manager_agent.tools.manifest import CancellationToken


class PackageTargetResolver:
    """Resolve Package ID plus source/version without fuzzy name selection."""

    def __init__(self, inventory: PackageInventoryService) -> None:
        self._inventory = inventory

    def resolve(
        self,
        query: WingetPackageQuery,
        max_items: int,
        cancellation: CancellationToken | None = None,
    ) -> tuple[ResolvedWingetPackage, WingetPackageInventory]:
        """Refresh inventory and preserve every ambiguity or incomplete state."""
        snapshot = self._inventory.inventory(max_items, cancellation)
        return self.resolve_from_inventory(query, snapshot), snapshot

    def inspect(
        self,
        identity_digest: str,
        max_items: int,
        cancellation: CancellationToken | None = None,
    ) -> tuple[NormalizedWingetPackage | None, WingetPackageInventory]:
        """Refresh and find only the exact source-qualified identity digest."""
        resolved, snapshot = self.resolve(
            WingetPackageQuery(identity_digest=identity_digest),
            max_items,
            cancellation,
        )
        return resolved.selected, snapshot

    @staticmethod
    def resolve_from_inventory(
        query: WingetPackageQuery,
        inventory: WingetPackageInventory,
    ) -> ResolvedWingetPackage:
        """Resolve against one immutable inventory without names or fuzzy matching."""
        if inventory.state is not WingetInventoryState.COMPLETE:
            return _unresolved(query, (), "Package inventory is incomplete; selection is blocked.")
        if query.identity_digest is not None:
            matches = tuple(
                item
                for item in inventory.packages
                if item.identity.canonical_digest() == query.identity_digest
            )
        else:
            package_id = query.package_id
            if package_id is None:
                return _unresolved(query, (), "An exact Package ID is required.")
            matches = tuple(
                item
                for item in inventory.packages
                if item.package_id.casefold() == package_id.casefold()
                and (
                    query.installed_version is None
                    or item.installed_version == query.installed_version
                )
            )
        if len(matches) == 1:
            return ResolvedWingetPackage(
                query=query,
                selected=matches[0],
                ambiguous=False,
                reason="One exact official-source package identity matched.",
            )
        reason = (
            "The exact package identity is absent."
            if not matches
            else "Multiple package versions matched; select an exact identity."
        )
        return _unresolved(query, matches, reason)


def _unresolved(
    query: WingetPackageQuery,
    candidates: tuple[NormalizedWingetPackage, ...],
    reason: str,
) -> ResolvedWingetPackage:
    return ResolvedWingetPackage(
        query=query,
        candidates=candidates[:100],
        ambiguous=True,
        reason=reason,
    )
