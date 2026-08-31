"""Default-deny review routing; business policies retain all mutation authority."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.optimization_actions import (
    OptimizationCapability,
    RecommendationActionability,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupSafetyClassification,
    OptimizationRecommendation,
    PerformanceCategory,
    ProtectionLevel,
    RecommendationType,
    SystemOptimizationReport,
)


class OptimizationRoutingError(PermissionError):
    """A finite reason explaining why untrusted routing intent was rejected."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OptimizationRoutingDecision:
    """Local routing decision; no execution or privilege decision is included."""

    capability: OptimizationCapability
    actionability: RecommendationActionability
    reason_codes: tuple[str, ...]


class OptimizationActionPolicy:
    """Validate structured recommendation types against actual report evidence."""

    def evaluate(
        self, report: SystemOptimizationReport, recommendation: OptimizationRecommendation
    ) -> OptimizationRoutingDecision:
        """Allow navigation only when every referenced observation belongs to this report."""
        candidate_map = {item.candidate_id: item for item in report.cleanup_candidates}
        finding_map = {item.finding_id: item for item in report.performance_findings}
        references = recommendation.evidence_references
        if (
            len(candidate_map) != len(report.cleanup_candidates)
            or len(finding_map) != len(report.performance_findings)
            or set(candidate_map) & set(finding_map)
            or len(set(references)) != len(references)
            or len(references) > 100
            or any(item not in candidate_map and item not in finding_map for item in references)
        ):
            raise OptimizationRoutingError("EVIDENCE_REFERENCE_INVALID")
        kind = recommendation.recommendation_type
        if kind is RecommendationType.MANUAL_REVIEW:
            return self._decision(OptimizationCapability.NONE, "MANUAL_REVIEW", review=True)
        if kind is RecommendationType.NO_ACTION_NEEDED:
            if any(
                item.category is not PerformanceCategory.NO_CLEAR_BOTTLENECK
                for item in report.performance_findings
            ):
                return self._blocked("NO_ACTION_EVIDENCE_CONTRADICTED")
            return OptimizationRoutingDecision(
                OptimizationCapability.NONE,
                RecommendationActionability.INFORMATIONAL,
                ("NO_ACTION_NEEDED",),
            )
        if not references:
            return self._blocked("EVIDENCE_REQUIRED")
        if kind is RecommendationType.REVIEW_SERVICE:
            return self._decision(
                OptimizationCapability.SERVICE_REVIEW, "SERVICE_REVIEW_ONLY", review=True
            )
        finding_routes = {
            RecommendationType.REVIEW_STARTUP_ITEM: (
                OptimizationCapability.STARTUP_REVIEW,
                {PerformanceCategory.STARTUP_LOAD},
            ),
            RecommendationType.REVIEW_HIGH_RESOURCE_PROCESS: (
                OptimizationCapability.PROCESS_REVIEW,
                {
                    PerformanceCategory.CPU_PRESSURE,
                    PerformanceCategory.MEMORY_PRESSURE,
                    PerformanceCategory.BACKGROUND_PROCESS_LOAD,
                },
            ),
            RecommendationType.REVIEW_INSTALLED_SOFTWARE: (
                OptimizationCapability.SOFTWARE_REVIEW,
                {PerformanceCategory.POSSIBLE_SOFTWARE_BLOAT},
            ),
            RecommendationType.FREE_DISK_SPACE: (
                OptimizationCapability.STORAGE_OVERVIEW,
                {PerformanceCategory.DISK_SPACE_PRESSURE},
            ),
        }
        if kind in finding_routes:
            capability, categories = finding_routes[kind]
            if any(
                reference not in finding_map or finding_map[reference].category not in categories
                for reference in references
            ):
                return self._blocked("FINDING_TYPE_MISMATCH")
            return self._decision(
                capability,
                "FRESH_DOMAIN_SELECTION_REQUIRED",
                review=capability is OptimizationCapability.STORAGE_OVERVIEW,
            )
        candidates = tuple(candidate_map[item] for item in references if item in candidate_map)
        if len(candidates) != len(references):
            return self._blocked("CANDIDATE_EVIDENCE_REQUIRED")
        if any(
            item.protection_level
            in {
                ProtectionLevel.PROTECTED,
                ProtectionLevel.STRONGLY_PROTECTED,
                ProtectionLevel.SYSTEM_PROTECTED,
                ProtectionLevel.UNKNOWN,
            }
            or item.safety_classification
            in {
                CleanupSafetyClassification.PROTECTED,
                CleanupSafetyClassification.BLOCKED,
                CleanupSafetyClassification.UNKNOWN,
            }
            for item in candidates
        ):
            return self._blocked("PROTECTED_SOURCE")
        return self._candidate_route(kind, candidates)

    def _candidate_route(
        self, kind: RecommendationType, candidates: tuple[CleanupCandidate, ...]
    ) -> OptimizationRoutingDecision:
        if kind is RecommendationType.REVIEW_SOFTWARE_RESIDUAL:
            if not all(
                item.source_reference is not None
                and item.source_reference.origin is CleanupEvidenceOrigin.STAGE4D3_REPORT
                for item in candidates
            ):
                return self._blocked("RESIDUAL_PROVENANCE_REQUIRED")
            return self._decision(
                OptimizationCapability.RESIDUAL_REVIEW, "FRESH_RESIDUAL_FLOW_REQUIRED"
            )
        personal = {
            RecommendationType.REVIEW_LARGE_FILES: CleanupCategory.LARGE_FILE,
            RecommendationType.REVIEW_INACTIVE_FILES: CleanupCategory.INACTIVE_LARGE_FILE,
            RecommendationType.REVIEW_DUPLICATES: CleanupCategory.DUPLICATE_FILE,
        }
        if kind in personal:
            if any(item.category is not personal[kind] for item in candidates):
                return self._blocked("PERSONAL_CATEGORY_MISMATCH")
            if not all(
                item.source_reference is not None
                and item.source_reference.origin is CleanupEvidenceOrigin.STAGE1_REPORT
                for item in candidates
            ):
                return self._blocked("PERSONAL_PROVENANCE_REQUIRED")
            return self._decision(
                OptimizationCapability.PERSONAL_STORAGE_REVIEW, "STAGE1_SCOPE_REQUIRED"
            )
        if kind is RecommendationType.REVIEW_RECYCLE_BIN:
            if any(item.category is not CleanupCategory.RECYCLE_BIN_CONTENT for item in candidates):
                return self._blocked("BIN_CATEGORY_MISMATCH")
            return self._decision(
                OptimizationCapability.RECYCLE_BIN_REVIEW, "INDEPENDENT_BIN_PREVIEW_REQUIRED"
            )
        direct = {
            RecommendationType.REVIEW_TEMP_STORAGE: (
                CleanupCategory.USER_TEMP,
                "current-user-temp",
            ),
            RecommendationType.REVIEW_APPLICATION_CACHE: (
                CleanupCategory.APPLICATION_CACHE,
                "directx-shader-cache",
            ),
            RecommendationType.REVIEW_CRASH_DUMPS: (
                CleanupCategory.CRASH_DUMP,
                "current-user-crash-dumps",
            ),
        }
        if kind not in direct:
            return self._blocked("UNSUPPORTED_RECOMMENDATION")
        category, source = direct[kind]
        if any(
            item.category is not category
            or item.source != source
            or item.source_reference is None
            or item.source_reference.origin is not CleanupEvidenceOrigin.KNOWN_LOCATION
            for item in candidates
        ):
            return OptimizationRoutingDecision(
                OptimizationCapability.NONE,
                RecommendationActionability.NOT_CURRENTLY_SUPPORTED,
                ("SOURCE_NOT_IN_DIRECT_V1_ALLOWLIST",),
            )
        return self._decision(
            OptimizationCapability.CLEANUP_REVIEW, "FRESH_CLEANUP_ASSESSMENT_REQUIRED"
        )

    @staticmethod
    def _decision(
        capability: OptimizationCapability, reason: str, *, review: bool = False
    ) -> OptimizationRoutingDecision:
        return OptimizationRoutingDecision(
            capability,
            RecommendationActionability.REVIEW_ONLY
            if review
            else RecommendationActionability.ROUTABLE,
            (reason,),
        )

    @staticmethod
    def _blocked(reason: str) -> OptimizationRoutingDecision:
        return OptimizationRoutingDecision(
            OptimizationCapability.NONE,
            RecommendationActionability.BLOCKED,
            (reason,),
        )
