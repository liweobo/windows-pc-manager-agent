"""Deterministic Stage 4E1 platform fake and registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.fixtures.system_diagnostics import FakeSystemPlatform

from pc_manager_agent.domain.system_diagnostics import (
    CollectorOutcome,
    CollectorState,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    ObservationAvailability,
    OptimizationEvidence,
    OwnershipConfidence,
    ScanScopeDecision,
    StorageAnalysisResult,
    StorageObservation,
)
from pc_manager_agent.orchestration.optimization_recommendation_engine import (
    OptimizationRecommendationEngine,
)
from pc_manager_agent.orchestration.performance_diagnostic_engine import (
    PerformanceDiagnosticEngine,
)
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.safety.cleanup_candidate_policy import CleanupCandidatePolicy
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.system_optimization import (
    OptimizationCleanupCandidateTool,
    OptimizationPerformanceTool,
    OptimizationRecommendationTool,
    OptimizationSnapshotTool,
    OptimizationStorageAnalysisTool,
)


def build_system_snapshot() -> SystemSnapshot:
    """Create a complete deterministic point-in-time snapshot."""
    platform = FakeSystemPlatform()

    class NotCancelled:
        def cancellation_requested(self) -> bool:
            return False

    cancellation = NotCancelled()
    disks, _disk_warnings = platform.collect_disks()
    processes, _process_warnings = platform.collect_processes(0.1, 100, cancellation)
    startup, _startup_warnings, _startup_truncated = platform.collect_startup(100)
    services, _service_warnings, _service_truncated = platform.collect_services(100)
    software, _software_warnings, _software_truncated = platform.collect_software(100)
    outcomes = tuple(
        CollectorOutcome(collector=item, state=CollectorState.SUCCEEDED, item_count=1)
        for item in SystemCollector
    )
    return SystemSnapshot(
        system_info=platform.collect_system_info(),
        cpu=platform.collect_cpu(3, 0.1, cancellation),
        memory=platform.collect_memory(),
        disks=disks,
        processes=processes,
        startup_entries=startup,
        services=services,
        software=software,
        outcomes=outcomes,
    )


class FakeOptimizationPlatform:
    """Expose only query methods and record their bounded arguments."""

    def __init__(self) -> None:
        self.snapshot_calls = 0
        self.storage_calls = 0

    def collect_system_snapshot(
        self,
        *,
        collectors: tuple[SystemCollector, ...],
        sample_count: int,
        sample_interval_seconds: float,
        max_processes: int,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> SystemSnapshot:
        assert 2 <= sample_count <= 10
        assert 0.1 <= sample_interval_seconds <= 2.0
        assert max_processes <= 2_000
        assert max_items <= 20_000
        assert collectors
        self.snapshot_calls += 1
        snapshot = build_system_snapshot()
        selected = set(collectors)
        return snapshot.model_copy(
            update={
                "system_info": snapshot.system_info
                if SystemCollector.SYSTEM_INFO in selected
                else None,
                "cpu": snapshot.cpu if SystemCollector.CPU in selected else None,
                "memory": snapshot.memory if SystemCollector.MEMORY in selected else None,
                "disks": snapshot.disks if SystemCollector.DISKS in selected else (),
                "processes": snapshot.processes if SystemCollector.PROCESSES in selected else None,
                "startup_entries": snapshot.startup_entries
                if SystemCollector.STARTUP in selected
                else (),
                "services": snapshot.services if SystemCollector.SERVICES in selected else (),
                "software": snapshot.software if SystemCollector.SOFTWARE in selected else (),
                "outcomes": tuple(item for item in snapshot.outcomes if item.collector in selected),
            }
        )

    def analyze_storage(
        self,
        *,
        authorized_roots: tuple[Path, ...],
        max_objects: int,
        timeout_seconds: float,
        minimum_large_file_bytes: int,
        inactive_days: int,
        cancellation: CancellationSignal,
    ) -> StorageAnalysisResult:
        assert max_objects <= 100_000
        assert timeout_seconds <= 600
        assert minimum_large_file_bytes >= 1024**2
        assert inactive_days >= 1
        self.storage_calls += 1
        return StorageAnalysisResult(
            observations=(
                StorageObservation(
                    category=CleanupCategory.USER_TEMP,
                    source="test-temp",
                    path=Path("C:/Users/test/AppData/Local/Temp"),
                    observed_size_bytes=2 * 1024**3,
                    item_count=20,
                    oldest_modified_at=datetime(2025, 1, 1, tzinfo=UTC),
                    newest_modified_at=datetime(2025, 1, 1, tzinfo=UTC),
                    availability=ObservationAvailability.AVAILABLE,
                    scope_decision=ScanScopeDecision.METADATA_ONLY,
                    ownership_confidence=OwnershipConfidence.HIGH,
                    evidence=(
                        OptimizationEvidence.KNOWN_LOCATION_ALLOWLIST,
                        OptimizationEvidence.DIRECT_FILE_METADATA,
                    ),
                ),
            )
        )


def build_optimization_registry(
    platform: FakeOptimizationPlatform | None = None,
) -> ToolRegistry:
    """Register exactly the five Stage 4E1 read-only tools."""
    adapter = platform or FakeOptimizationPlatform()
    registry = ToolRegistry()
    for tool in (
        OptimizationSnapshotTool(adapter),
        OptimizationStorageAnalysisTool(adapter),
        OptimizationCleanupCandidateTool(CleanupCandidatePolicy()),
        OptimizationPerformanceTool(PerformanceDiagnosticEngine()),
        OptimizationRecommendationTool(OptimizationRecommendationEngine()),
    ):
        registry.register(tool)
    return registry
