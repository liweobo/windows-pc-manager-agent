"""Short-lived, single-use navigation contexts. They never contain write authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationActionRoute,
    OptimizationCapability,
    PreparationStatus,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.orchestration.optimization_capabilities import capability_surface
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError
from pc_manager_agent.tools.manifest import CancellationToken


class DomainReviewContext(FrozenModel):
    """Local navigation inputs; no selected execution target, confirmation or command."""

    context_id: UUID = Field(default_factory=uuid4)
    route: OptimizationActionRoute
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    candidate_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    authorized_root_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    uninstall_transaction_id: UUID | None = None


class OptimizationHandoffStore:
    """Bounded session-local review contexts, atomically removed on navigation."""

    def __init__(self, *, maximum: int = 100) -> None:
        if not 1 <= maximum <= 1_000:
            raise ValueError("Invalid handoff bound")
        self._maximum = maximum
        self._contexts: dict[UUID, DomainReviewContext] = {}
        self._lock = RLock()

    def save(self, context: DomainReviewContext) -> None:
        """Refuse replacement and capacity overflow; no persistent/resumable authority."""
        context = DomainReviewContext.model_validate_json(context.model_dump_json())
        with self._lock:
            now = datetime.now(UTC)
            self._contexts = {
                key: item for key, item in self._contexts.items() if item.route.expires_at > now
            }
            if context.context_id in self._contexts or len(self._contexts) >= self._maximum:
                raise OptimizationRoutingError("HANDOFF_CAPACITY_OR_DUPLICATE")
            if context.route.expires_at <= now:
                raise OptimizationRoutingError("HANDOFF_EXPIRED")
            self._contexts[context.context_id] = context

    def take(self, context_id: UUID, route_id: UUID) -> DomainReviewContext:
        """Consume one exact context. Even a mismatched attempt cannot replay it."""
        with self._lock:
            context = self._contexts.pop(context_id, None)
        if context is None or context.route.route_id != route_id:
            raise OptimizationRoutingError("HANDOFF_UNKNOWN_OR_MISMATCH")
        if context.route.expires_at <= datetime.now(UTC):
            raise OptimizationRoutingError("HANDOFF_EXPIRED")
        return context

    def clear(self) -> None:
        """Discard navigation on shutdown; no business operation is cancelled or undone."""
        with self._lock:
            self._contexts.clear()


class DomainReviewPreparationService:
    """Prepare source-checked navigation before the domain's fresh target selection.

    NEEDS_TARGET_SELECTION deliberately does not claim the target has passed Fresh
    revalidation. The destination's existing service must do that before any Preview.
    """

    def __init__(
        self,
        capability: OptimizationCapability,
        store: OptimizationHandoffStore,
        resolve_context: Callable[
            [OptimizationActionRoute, SystemOptimizationReport, CancellationToken],
            DomainReviewContext,
        ],
    ) -> None:
        if capability is OptimizationCapability.NONE:
            raise ValueError("No preparation exists for NONE")
        self._capability = capability
        self._store = store
        self._resolve_context = resolve_context

    @property
    def capability(self) -> OptimizationCapability:
        """Return the fixed capability installed by the composition root."""
        return self._capability

    def available(self) -> bool:
        """This adapter prepares only local navigation, not platform execution."""
        return True

    def prepare(
        self,
        route: OptimizationActionRoute,
        report: SystemOptimizationReport,
        cancellation: CancellationToken,
    ) -> OptimizationActionPreparationResult:
        """Resolve local provenance and issue one context for a domain-owned review UI."""
        if route.target_capability is not self.capability:
            raise OptimizationRoutingError("HANDOFF_CAPABILITY_MISMATCH")
        if cancellation.is_cancelled:
            raise OptimizationRoutingError("PREPARATION_CANCELLED")
        context = self._resolve_context(route, report, cancellation)
        if context.route != route:
            raise OptimizationRoutingError("HANDOFF_SOURCE_SUBSTITUTED")
        if cancellation.is_cancelled:
            raise OptimizationRoutingError("PREPARATION_CANCELLED")
        self._store.save(context)
        return OptimizationActionPreparationResult(
            route_id=route.route_id,
            recommendation_id=route.recommendation_id,
            target_domain=route.target_domain,
            status=PreparationStatus.NEEDS_TARGET_SELECTION,
            fresh_context_id=context.context_id,
            next_ui_surface=capability_surface(self.capability),
            blocked_reason_codes=("DOMAIN_FRESH_SELECTION_AND_CONFIRMATIONS_STILL_REQUIRED",),
        )
