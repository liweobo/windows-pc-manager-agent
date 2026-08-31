"""Small, bounded invalidation service instead of a system-wide event bus."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionRoute,
    OptimizationTargetDomain,
)
from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError


class RecommendationInvalidationService:
    """Invalidate old evidence after a domain change without mutating original reports."""

    def __init__(
        self, *, now: Callable[[], datetime] | None = None, max_invalidations: int = 10000
    ) -> None:
        if max_invalidations < 1:
            raise ValueError("Invalidation limit must be positive")
        self._now = now or (lambda: datetime.now(UTC))
        self._limit = max_invalidations
        self._domains: dict[OptimizationTargetDomain, datetime] = {}
        self._snapshots: set[UUID] = set()
        self._references: set[tuple[UUID, UUID]] = set()
        self._saturated = False
        self._lock = RLock()

    def invalidate_by_domain(self, domain: OptimizationTargetDomain) -> None:
        """Conservatively invalidate older domain observations after a domain state change."""
        with self._lock:
            self._domains[domain] = self._now()

    def invalidate_by_snapshot(self, snapshot_id: UUID) -> None:
        """Reject every recommendation derived from one expired/replaced snapshot."""
        with self._lock:
            self._snapshots.add(snapshot_id)
            self._bound()

    def invalidate_by_target_identity(
        self, source_report_id: UUID, recommendation_ids: tuple[UUID, ...]
    ) -> None:
        """Invalidate references matched locally to a changed domain target identity."""
        with self._lock:
            self._references.update((source_report_id, item) for item in recommendation_ids)
            self._bound()

    def require_current(
        self, route: OptimizationActionRoute, report: SystemOptimizationReport
    ) -> None:
        """Fail closed on changed, expired or untrackable source evidence."""
        with self._lock:
            changed = self._domains.get(route.target_domain)
            if (
                self._saturated
                or route.source_snapshot_id in self._snapshots
                or (route.source_report_id, route.recommendation_id) in self._references
                or (changed is not None and report.snapshot.collected_at <= changed)
                or self._now() >= route.expires_at
            ):
                raise OptimizationRoutingError("RECOMMENDATION_STALE")

    def _bound(self) -> None:
        if len(self._snapshots) + len(self._references) > self._limit:
            # Never evict an invalidation and accidentally resurrect old intent.
            self._saturated = True
            self._snapshots.clear()
            self._references.clear()
