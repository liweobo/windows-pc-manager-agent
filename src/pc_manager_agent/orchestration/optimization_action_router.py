"""Stage 4E3 recommendation-to-domain preparation, with no write execution method."""

from __future__ import annotations

from pc_manager_agent.audit.optimization_actions import OptimizationActionAuditLogger
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationActionRoute,
    OptimizationCapability,
    OptimizationRecommendationReference,
    RecommendationActionability,
)
from pc_manager_agent.orchestration.optimization_action_resolver import (
    RecommendationActionResolver,
    optimization_source_digest,
)
from pc_manager_agent.orchestration.optimization_capabilities import (
    OptimizationDomainCapabilityRegistry,
)
from pc_manager_agent.orchestration.optimization_invalidation import (
    RecommendationInvalidationService,
)
from pc_manager_agent.orchestration.optimization_report_store import (
    OptimizationReportSessionStore,
    OptimizationReportUnavailableError,
)
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError
from pc_manager_agent.tools.manifest import CancellationToken


class OptimizationActionRouter:
    """Load trusted local evidence and hand off only to sealed preparation interfaces."""

    def __init__(
        self,
        reports: OptimizationReportSessionStore,
        resolver: RecommendationActionResolver,
        capabilities: OptimizationDomainCapabilityRegistry,
        invalidation: RecommendationInvalidationService,
        audit: OptimizationActionAuditLogger,
    ) -> None:
        self._reports = reports
        self._resolver = resolver
        self._capabilities = capabilities
        self._invalidation = invalidation
        self._audit = audit

    def inspect(self, reference: OptimizationRecommendationReference) -> OptimizationActionRoute:
        """Resolve and audit navigation eligibility; callers cannot provide a Route body."""
        reference = OptimizationRecommendationReference.model_validate_json(
            reference.model_dump_json()
        )
        try:
            report = self._reports.get(reference.source_report_id)
            route = self._resolver.resolve(report, reference.recommendation_id)
            self._invalidation.require_current(route, report)
            if (
                route.target_capability is not OptimizationCapability.NONE
                and not self._capabilities.available(route.target_capability)
            ):
                route = OptimizationActionRoute.model_validate(
                    route.model_dump()
                    | {
                        "actionability": RecommendationActionability.NOT_CURRENTLY_SUPPORTED,
                        "route_reason_codes": ("CAPABILITY_UNAVAILABLE",),
                    }
                )
        except OptimizationReportUnavailableError as exc:
            self._audit.rejected(reference, "SOURCE_REPORT_UNAVAILABLE")
            raise OptimizationRoutingError("SOURCE_REPORT_UNAVAILABLE") from exc
        except OptimizationRoutingError as exc:
            self._audit.rejected(reference, exc.reason_code)
            raise
        self._audit.route_decided(route)
        return route

    def prepare(
        self,
        reference: OptimizationRecommendationReference,
        cancellation: CancellationToken | None = None,
    ) -> OptimizationActionPreparationResult:
        """Repeat source checks and prepare review; never resolve a domain confirmation."""
        route = self.inspect(reference)
        if (
            route.actionability
            not in {
                RecommendationActionability.ROUTABLE,
                RecommendationActionability.REVIEW_ONLY,
            }
            or route.target_capability is OptimizationCapability.NONE
        ):
            self._audit.rejected(reference, "ROUTE_NOT_PREPARABLE")
            raise OptimizationRoutingError("ROUTE_NOT_PREPARABLE")
        token = cancellation or CancellationToken()
        try:
            report = self._reports.get(reference.source_report_id)
            if optimization_source_digest(report) != route.source_digest:
                raise OptimizationRoutingError("SOURCE_CHANGED")
            self._invalidation.require_current(route, report)
            result = self._capabilities.prepare(route, report, token)
            current = self._reports.get(reference.source_report_id)
            if optimization_source_digest(current) != route.source_digest:
                raise OptimizationRoutingError("SOURCE_CHANGED")
            self._invalidation.require_current(route, current)
        except OptimizationReportUnavailableError as exc:
            self._audit.rejected(reference, "SOURCE_REPORT_UNAVAILABLE")
            raise OptimizationRoutingError("SOURCE_REPORT_UNAVAILABLE") from exc
        except OptimizationRoutingError as exc:
            self._audit.rejected(reference, exc.reason_code)
            raise
        self._audit.prepared(route, result)
        return result
