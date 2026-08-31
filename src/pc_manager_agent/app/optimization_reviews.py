"""Composition of reference-only review navigation with existing business services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pc_manager_agent.audit.optimization_actions import OptimizationActionAuditLogger
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionRoute,
    OptimizationCapability,
)
from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.orchestration.optimization_action_resolver import RecommendationActionResolver
from pc_manager_agent.orchestration.optimization_action_router import OptimizationActionRouter
from pc_manager_agent.orchestration.optimization_capabilities import (
    OptimizationDomainCapabilityRegistry,
)
from pc_manager_agent.orchestration.optimization_handoffs import (
    DomainReviewContext,
    DomainReviewPreparationService,
    OptimizationHandoffStore,
)
from pc_manager_agent.orchestration.optimization_invalidation import (
    RecommendationInvalidationService,
)
from pc_manager_agent.orchestration.optimization_outcomes import OptimizationOutcomeCoordinator
from pc_manager_agent.orchestration.optimization_session import OptimizationSessionService
from pc_manager_agent.safety.optimization_actions import (
    OptimizationActionPolicy,
    OptimizationRoutingError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.optimization_actions import (
    build_optimization_action_registry,
)

if TYPE_CHECKING:
    from pc_manager_agent.app.runtime import ApplicationRuntime


@dataclass(frozen=True, slots=True)
class OptimizationReviewServices:
    """One app-wide review bundle, sharing cancellation, invalidation and session state."""

    registry: ToolRegistry
    router: OptimizationActionRouter
    sessions: OptimizationSessionService
    handoffs: OptimizationHandoffStore
    invalidation: RecommendationInvalidationService
    outcomes: OptimizationOutcomeCoordinator


class RepositoryDomainReviewResolver:
    """Resolve exact report provenance, without names, file searches or model input."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        self._runtime = runtime

    def resolve(
        self,
        route: OptimizationActionRoute,
        report: SystemOptimizationReport,
        cancellation: CancellationToken,
    ) -> DomainReviewContext:
        """Check upstream identity references; targets remain unchecked in their own UI."""
        candidates = tuple(
            item
            for item in report.cleanup_candidates
            if item.candidate_id in route.evidence_references
        )
        candidate_ids = tuple(item.candidate_id for item in candidates)
        if route.target_capability is OptimizationCapability.RESIDUAL_REVIEW:
            transactions = set()
            for candidate in candidates:
                if cancellation.is_cancelled:
                    raise OptimizationRoutingError("PREPARATION_CANCELLED")
                source = candidate.source_reference
                if source is None or source.upstream_report_id is None:
                    raise OptimizationRoutingError("RESIDUAL_PROVENANCE_MISSING")
                upstream = self._runtime.software_residual_repository.get_report(
                    source.upstream_report_id
                )
                if not any(
                    item.candidate_id == source.upstream_candidate_id
                    for item in upstream.candidates
                ):
                    raise OptimizationRoutingError("RESIDUAL_CANDIDATE_MISSING")
                context = self._runtime.software_residual_repository.get_context(
                    upstream.context_id
                )
                if (
                    not context.eligible_for_analysis
                    or context.transaction_id != upstream.uninstall_transaction_id
                ):
                    raise OptimizationRoutingError("RESIDUAL_CONTEXT_INELIGIBLE")
                transactions.add(context.transaction_id)
            if len(transactions) != 1:
                raise OptimizationRoutingError("RESIDUAL_REQUIRES_ONE_UNINSTALL_CONTEXT")
            return DomainReviewContext(
                route=route, uninstall_transaction_id=next(iter(transactions))
            )
        if route.target_capability is OptimizationCapability.PERSONAL_STORAGE_REVIEW:
            roots = self._runtime.authorized_paths.list_authorized()
            root_ids = set()
            for candidate in candidates:
                source = candidate.source_reference
                if source is None or source.upstream_record_id is None:
                    raise OptimizationRoutingError("PERSONAL_PROVENANCE_MISSING")
                record = self._runtime.analysis_results.get_record(source.upstream_record_id)
                if candidate.path != record.metadata.path:
                    raise OptimizationRoutingError("PERSONAL_SOURCE_CHANGED")
                self._runtime.authorized_paths.require_authorized_file(record.metadata.path)
                matches = tuple(root for root in roots if root.path == record.metadata.scan_root)
                if len(matches) != 1:
                    raise OptimizationRoutingError("PERSONAL_SCOPE_REVOKED_OR_CHANGED")
                root_ids.add(matches[0].path_id)
            if not root_ids:
                raise OptimizationRoutingError("PERSONAL_SCOPE_MISSING")
            # Navigation selects no file. Stage 1 rescans only these roots; Stage 2 later
            # resolves selected file identities afresh and applies its independent gates.
            return DomainReviewContext(route=route, authorized_root_ids=tuple(sorted(root_ids)))
        return DomainReviewContext(route=route, candidate_ids=candidate_ids)


def build_optimization_review_services(runtime: ApplicationRuntime) -> OptimizationReviewServices:
    """Install finite preparation interfaces; never borrow a writer's registry or guard."""
    audit = OptimizationActionAuditLogger(runtime.audit)
    handoffs = OptimizationHandoffStore()
    invalidation = RecommendationInvalidationService()
    capabilities = OptimizationDomainCapabilityRegistry()
    source = RepositoryDomainReviewResolver(runtime)
    for capability in OptimizationCapability:
        if capability is not OptimizationCapability.NONE:
            capabilities.register(
                DomainReviewPreparationService(capability, handoffs, source.resolve)
            )
    capabilities.seal()
    router = OptimizationActionRouter(
        runtime.optimization_report_store,
        RecommendationActionResolver(OptimizationActionPolicy()),
        capabilities,
        invalidation,
        audit,
    )
    sessions = OptimizationSessionService(runtime.optimization_session_repository, router, audit)
    return OptimizationReviewServices(
        build_optimization_action_registry(router, sessions),
        router,
        sessions,
        handoffs,
        invalidation,
        OptimizationOutcomeCoordinator(runtime.optimization_result_reader, sessions, invalidation),
    )
