"""The exact five registered R0 tools for Stage 4E1."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_optimization import (
    CleanupAnalysisRequest,
    CleanupAnalysisResult,
    PerformanceAnalysisRequest,
    PerformanceAnalysisResult,
    RecommendationRequest,
    RecommendationResult,
    SnapshotRequest,
    SnapshotResult,
    StorageAnalysisRequest,
    StorageAnalysisResult,
    StorageObservation,
)
from pc_manager_agent.orchestration.optimization_evidence import OptimizationEvidenceSource
from pc_manager_agent.orchestration.optimization_recommendation_engine import (
    OptimizationRecommendationEngine,
)
from pc_manager_agent.orchestration.performance_diagnostic_engine import (
    PerformanceDiagnosticEngine,
)
from pc_manager_agent.platform_support.system_optimization import SystemOptimizationPlatform
from pc_manager_agent.safety.cleanup_candidate_policy import CleanupCandidatePolicy
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


def _manifest(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    max_batch_size: int,
    supports_cancellation: bool = False,
) -> ToolManifest:
    """Construct an immutable manifest with an explicit zero-change postcondition."""
    return ToolManifest(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        risk_level=RiskLevel.R0,
        required_permissions=("current-user-query",),
        read_only=True,
        idempotent=False,
        supports_cancellation=supports_cancellation,
        rollback_level=RollbackLevel.NONE,
        preconditions=("exact plan is confirmed", "audit store is available"),
        postconditions=("system and user data changes equal zero",),
        timeout_seconds=600.0,
        max_batch_size=max_batch_size,
        audit_fields=("source counts", "partial status", "duration"),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=False,
    )


class OptimizationSnapshotTool:
    """Collect a current system snapshot through a query-only platform contract."""

    def __init__(self, platform: SystemOptimizationPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="optimization.snapshot",
            description=(
                "Collect bounded CPU, memory, disk, process, startup, service and software data"
            ),
            input_model=SnapshotRequest,
            output_model=SnapshotResult,
            max_batch_size=20_000,
            supports_cancellation=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Collect only when the registry supplied the declared request type."""
        if not isinstance(request, SnapshotRequest):
            raise TypeError("OptimizationSnapshotTool received an unexpected input model")
        return SnapshotResult(
            system=self._platform.collect_system_snapshot(
                collectors=request.collectors,
                sample_count=request.sample_count,
                sample_interval_seconds=request.sample_interval_seconds,
                max_processes=request.max_processes,
                max_items=request.max_items,
                cancellation=cancellation,
            )
        )


class OptimizationStorageAnalysisTool:
    """Collect bounded file metadata and documented Windows storage summaries."""

    def __init__(
        self,
        platform: SystemOptimizationPlatform,
        evidence_source: OptimizationEvidenceSource | None = None,
    ) -> None:
        self._platform = platform
        self._evidence_source = evidence_source
        self._manifest = _manifest(
            name="optimization.storage.analyze",
            description="Analyze metadata in exact known or explicitly authorized storage roots",
            input_model=StorageAnalysisRequest,
            output_model=StorageAnalysisResult,
            max_batch_size=100_000,
            supports_cancellation=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Delegate metadata-only observation using locally bounded arguments."""
        if not isinstance(request, StorageAnalysisRequest):
            raise TypeError("OptimizationStorageAnalysisTool received an unexpected input model")
        result = self._platform.analyze_storage(
            authorized_roots=request.authorized_roots,
            max_objects=request.max_objects,
            timeout_seconds=request.timeout_seconds,
            minimum_large_file_bytes=request.minimum_large_file_bytes,
            inactive_days=request.inactive_days,
            cancellation=cancellation,
        )
        if self._evidence_source is None or cancellation.cancellation_requested():
            return result
        historical = self._evidence_source.observations(
            request.authorized_roots,
            max_items=request.max_objects,
            cancellation=cancellation,
        )
        by_object: dict[str, StorageObservation] = {}
        for observation in (*result.observations, *historical.observations):
            key = (
                str(observation.path).casefold()
                if observation.path is not None
                else f"{observation.category.value}:{observation.source}"
            )
            by_object[key] = observation
        return StorageAnalysisResult(
            observations=tuple(by_object.values()),
            partial_sources=tuple(
                dict.fromkeys((*result.partial_sources, *historical.partial_sources))
            ),
            skipped_sources=tuple(
                dict.fromkeys((*result.skipped_sources, *historical.skipped_sources))
            ),
            truncated=result.truncated or historical.truncated,
        )


class OptimizationCleanupCandidateTool:
    """Classify observations without selecting or changing any object."""

    def __init__(self, policy: CleanupCandidatePolicy) -> None:
        self._policy = policy
        self._manifest = _manifest(
            name="optimization.cleanup_candidates.analyze",
            description="Classify storage observations by safety, protection and confidence",
            input_model=CleanupAnalysisRequest,
            output_model=CleanupAnalysisResult,
            max_batch_size=100_000,
            supports_cancellation=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return partial classifications if cooperative cancellation is requested."""
        if not isinstance(request, CleanupAnalysisRequest):
            raise TypeError("OptimizationCleanupCandidateTool received an unexpected input model")
        candidates = []
        for observation in request.observations:
            if cancellation.cancellation_requested():
                break
            candidates.append(
                self._policy.classify(observation, inactive_days=request.inactive_days)
            )
        return CleanupAnalysisResult(candidates=tuple(candidates))


class OptimizationPerformanceTool:
    """Run deterministic multi-factor performance rules."""

    def __init__(self, engine: PerformanceDiagnosticEngine) -> None:
        self._engine = engine
        self._manifest = _manifest(
            name="optimization.performance.analyze",
            description="Evaluate available performance counters using conservative rules",
            input_model=PerformanceAnalysisRequest,
            output_model=PerformanceAnalysisResult,
            max_batch_size=20_000,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Analyze a validated snapshot without accessing Windows directly."""
        if not isinstance(request, PerformanceAnalysisRequest):
            raise TypeError("OptimizationPerformanceTool received an unexpected input model")
        if cancellation.cancellation_requested():
            return PerformanceAnalysisResult(findings=())
        return PerformanceAnalysisResult(findings=self._engine.analyze(request.system))


class OptimizationRecommendationTool:
    """Build non-executable recommendations from structured local evidence."""

    def __init__(self, engine: OptimizationRecommendationEngine) -> None:
        self._engine = engine
        self._manifest = _manifest(
            name="optimization.recommendations",
            description="Create evidence-linked review suggestions with no execution authority",
            input_model=RecommendationRequest,
            output_model=RecommendationResult,
            max_batch_size=100_000,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Build advice only from validated candidates and findings."""
        if not isinstance(request, RecommendationRequest):
            raise TypeError("OptimizationRecommendationTool received an unexpected input model")
        if cancellation.cancellation_requested():
            return RecommendationResult(recommendations=())
        return RecommendationResult(
            recommendations=self._engine.build(request.goals, request.candidates, request.findings)
        )
