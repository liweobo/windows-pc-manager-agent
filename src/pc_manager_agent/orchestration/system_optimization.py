"""Confirmed goal-scoped orchestration for strictly read-only Stage 4E1."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.audit.system_optimization import SystemOptimizationAuditLogger
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.system_optimization import OptimizationConfirmationService
from pc_manager_agent.domain.system_optimization import (
    CleanupAnalysisResult,
    CleanupSafetyClassification,
    OptimizationPlan,
    OptimizationSnapshot,
    OptimizationToolName,
    PerformanceAnalysisResult,
    RecommendationResult,
    SnapshotResult,
    StorageAnalysisResult,
    SystemOptimizationReport,
)
from pc_manager_agent.orchestration.system_optimization_planner import (
    SystemOptimizationPlanCompiler,
)
from pc_manager_agent.safety.system_optimization import (
    OptimizationSafetyReview,
    SystemOptimizationSafetyValidator,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class SystemOptimizationOrchestrator:
    """Keep planning, review, confirmation, collection, analysis and audit separate."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        compiler: SystemOptimizationPlanCompiler,
        safety: SystemOptimizationSafetyValidator,
        confirmation: OptimizationConfirmationService,
        audit: SystemOptimizationAuditLogger,
    ) -> None:
        self._registry = registry
        self._compiler = compiler
        self._safety = safety
        self._confirmation = confirmation
        self._audit = audit

    def prepare(
        self, user_goal: str, authorized_root_ids: tuple[UUID, ...] = ()
    ) -> tuple[OptimizationPlan, OptimizationSafetyReview]:
        """Compile and independently review a plan without reading system state."""
        plan = self._compiler.compile(user_goal, authorized_root_ids)
        review = self._safety.review(plan)
        self._audit.plan_reviewed(
            plan, review.approved, tuple(item.message for item in review.issues)
        )
        return plan, review

    def request_confirmation(self, plan: OptimizationPlan) -> ConfirmationRequest:
        """Request approval only after a fresh safety review."""
        review = self._safety.review(plan)
        if not review.approved:
            raise ValueError("Optimization plan failed safety review")
        return self._confirmation.request(plan)

    def resolve_confirmation(
        self, confirmation_id: UUID, approved: bool, plan: OptimizationPlan
    ) -> ConfirmationRequest:
        """Resolve and audit the exact plan approval."""
        resolved = self._confirmation.resolve(confirmation_id, approved, plan)
        self._audit.confirmation_resolved(plan, approved)
        return resolved

    def execute(
        self, plan: OptimizationPlan, cancellation: CancellationToken | None = None
    ) -> SystemOptimizationReport:
        """Run only the confirmed dependency-complete R0 subset and build a report."""
        review = self._safety.review(plan)
        if not review.approved:
            raise ValueError("Optimization plan changed or failed execution-time review")
        self._confirmation.require_approved(plan)
        token = cancellation or CancellationToken()

        snapshot = self._registry.execute(
            OptimizationToolName.SNAPSHOT.value,
            {
                "collectors": plan.snapshot_collectors,
                "sample_count": plan.sample_count,
                "sample_interval_seconds": plan.sample_interval_seconds,
                "max_processes": min(plan.max_objects, 2_000),
                "max_items": min(plan.max_objects, 20_000),
            },
            token,
        )
        if not isinstance(snapshot, SnapshotResult):
            raise TypeError("optimization.snapshot returned an unexpected result")
        self._audit.tool_completed(
            plan, OptimizationToolName.SNAPSHOT.value, len(snapshot.system.outcomes)
        )

        storage = StorageAnalysisResult(observations=())
        cleanup = CleanupAnalysisResult(candidates=())
        if OptimizationToolName.STORAGE_ANALYZE in plan.tools:
            storage_result = self._registry.execute(
                OptimizationToolName.STORAGE_ANALYZE.value,
                {
                    "authorized_roots": plan.authorized_roots,
                    "max_objects": plan.max_objects,
                    "timeout_seconds": plan.timeout_seconds,
                    "minimum_large_file_bytes": plan.minimum_large_file_bytes,
                    "inactive_days": plan.inactive_days,
                },
                token,
            )
            if not isinstance(storage_result, StorageAnalysisResult):
                raise TypeError("optimization.storage.analyze returned an unexpected result")
            storage = storage_result
            self._audit.tool_completed(
                plan, OptimizationToolName.STORAGE_ANALYZE.value, len(storage.observations)
            )

            cleanup_result = self._registry.execute(
                OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE.value,
                {"observations": storage.observations, "inactive_days": plan.inactive_days},
                token,
            )
            if not isinstance(cleanup_result, CleanupAnalysisResult):
                raise TypeError(
                    "optimization.cleanup_candidates.analyze returned an unexpected result"
                )
            cleanup = cleanup_result
            self._audit.tool_completed(
                plan,
                OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE.value,
                len(cleanup.candidates),
            )

        performance = PerformanceAnalysisResult(findings=())
        if OptimizationToolName.PERFORMANCE_ANALYZE in plan.tools:
            performance_result = self._registry.execute(
                OptimizationToolName.PERFORMANCE_ANALYZE.value,
                {"system": snapshot.system},
                token,
            )
            if not isinstance(performance_result, PerformanceAnalysisResult):
                raise TypeError("optimization.performance.analyze returned an unexpected result")
            performance = performance_result
            self._audit.tool_completed(
                plan,
                OptimizationToolName.PERFORMANCE_ANALYZE.value,
                len(performance.findings),
            )

        recommendations = self._registry.execute(
            OptimizationToolName.RECOMMENDATIONS.value,
            {
                "goals": plan.goals,
                "candidates": cleanup.candidates,
                "findings": performance.findings,
            },
            token,
        )
        if not isinstance(recommendations, RecommendationResult):
            raise TypeError("optimization.recommendations returned an unexpected result")
        self._audit.tool_completed(
            plan,
            OptimizationToolName.RECOMMENDATIONS.value,
            len(recommendations.recommendations),
        )

        combined_snapshot = OptimizationSnapshot(
            system=snapshot.system,
            storage_observations=storage.observations,
            partial_sources=storage.partial_sources,
            skipped_sources=storage.skipped_sources,
        )
        potential_values = tuple(
            item.potential_reclaim_bytes
            for item in cleanup.candidates
            if item.potential_reclaim_bytes is not None
        )
        protected = {
            CleanupSafetyClassification.PROTECTED,
            CleanupSafetyClassification.BLOCKED,
        }
        unknown = {CleanupSafetyClassification.UNKNOWN}
        report = SystemOptimizationReport(
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            snapshot=combined_snapshot,
            cleanup_candidates=cleanup.candidates,
            performance_findings=performance.findings,
            recommendations=recommendations.recommendations,
            observed_bytes=sum(item.observed_size_bytes for item in cleanup.candidates),
            potential_reclaim_bytes=sum(potential_values) if potential_values else None,
            protected_bytes=sum(
                item.observed_size_bytes
                for item in cleanup.candidates
                if item.safety_classification in protected
            ),
            unknown_bytes=sum(
                item.observed_size_bytes
                for item in cleanup.candidates
                if item.safety_classification in unknown
            ),
            partial_sources=storage.partial_sources,
            skipped_sources=storage.skipped_sources,
        )
        self._audit.report_completed(report)
        return report
