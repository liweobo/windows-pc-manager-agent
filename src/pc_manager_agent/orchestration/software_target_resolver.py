"""Fresh, conservative target resolution for installed software."""

from pc_manager_agent.domain.software_errors import (
    SoftwareAnalysisError,
    SoftwareAnalysisErrorCode,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    ResolvedSoftwareTarget,
    SoftwareTargetQuery,
)
from pc_manager_agent.orchestration.software_inventory import (
    SoftwareInventoryService,
    SoftwareInventorySnapshot,
)
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareTargetResolver:
    """Resolve an exact identity or return bounded candidates without auto-selection."""

    def __init__(self, inventory: SoftwareInventoryService, max_candidates: int = 100) -> None:
        self._inventory = inventory
        self._max_candidates = max_candidates

    def refresh(self, max_items: int, cancellation: CancellationToken) -> SoftwareInventorySnapshot:
        """Collect a new authoritative inventory for each decision."""
        return self._inventory.collect(max_items, cancellation)

    def resolve(
        self,
        query: SoftwareTargetQuery,
        max_items: int,
        cancellation: CancellationToken,
    ) -> tuple[ResolvedSoftwareTarget, SoftwareInventorySnapshot]:
        """Resolve by exact digest/name filters; substring matches remain ambiguous."""
        snapshot = self.refresh(max_items, cancellation)
        entries = snapshot.inventory.entries
        if query.identity_digest is not None:
            matches = tuple(
                item
                for item in entries
                if item.identity.canonical_digest() == query.identity_digest
            )
            if len(matches) == 1:
                return (
                    ResolvedSoftwareTarget(
                        query=query,
                        selected=matches[0],
                        reason="Exact source-qualified software identity matched.",
                    ),
                    snapshot,
                )
            return self._ambiguous(
                query, matches, "Exact identity is absent or no longer unique"
            ), snapshot
        if query.display_name is None:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_NOT_FOUND,
                "Software target query has no exact identity or display name",
            )
        name = query.display_name.casefold().strip()
        exact = tuple(item for item in entries if item.display_name.casefold() == name)
        filtered = self._filters(exact, query)
        if len(filtered) == 1:
            return (
                ResolvedSoftwareTarget(
                    query=query,
                    selected=filtered[0],
                    reason="One exact normalized display-name target matched all supplied fields.",
                ),
                snapshot,
            )
        if filtered:
            return self._ambiguous(
                query, filtered, "Multiple exact software targets matched"
            ), snapshot
        substring = tuple(item for item in entries if name in item.display_name.casefold())
        substring = self._filters(substring, query)
        return (
            self._ambiguous(
                query,
                substring,
                "No unique exact match; conservative search returned candidates",
            ),
            snapshot,
        )

    def inspect(
        self,
        identity_digest: str,
        max_items: int,
        cancellation: CancellationToken,
    ) -> tuple[NormalizedInstalledSoftware | None, SoftwareInventorySnapshot]:
        """Return one fresh exact identity match or None if it changed/disappeared."""
        snapshot = self.refresh(max_items, cancellation)
        matches = tuple(
            item
            for item in snapshot.inventory.entries
            if item.identity.canonical_digest() == identity_digest
        )
        return (matches[0] if len(matches) == 1 else None), snapshot

    @staticmethod
    def _filters(
        entries: tuple[NormalizedInstalledSoftware, ...],
        query: SoftwareTargetQuery,
    ) -> tuple[NormalizedInstalledSoftware, ...]:
        """Apply only caller-supplied exact publisher/version/scope constraints."""
        result = entries
        if query.publisher is not None:
            publisher = query.publisher.casefold().strip()
            result = tuple(
                item for item in result if (item.publisher or "").casefold() == publisher
            )
        if query.display_version is not None:
            version = query.display_version.casefold().strip()
            result = tuple(
                item for item in result if (item.display_version or "").casefold() == version
            )
        if query.scope is not None:
            result = tuple(item for item in result if item.scope is query.scope)
        if query.architecture is not None:
            result = tuple(item for item in result if item.architecture is query.architecture)
        return result

    def _ambiguous(
        self,
        query: SoftwareTargetQuery,
        candidates: tuple[NormalizedInstalledSoftware, ...],
        reason: str,
    ) -> ResolvedSoftwareTarget:
        """Bound candidates and mark the result unresolved even when the set is empty."""
        return ResolvedSoftwareTarget(
            query=query,
            candidates=candidates[: self._max_candidates],
            ambiguous=True,
            reason=reason,
        )
