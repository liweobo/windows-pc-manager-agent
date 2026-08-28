"""Query-only platform contract for Stage 4E1 optimization analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.system_diagnostics import SystemCollector, SystemSnapshot
from pc_manager_agent.domain.system_optimization import StorageAnalysisResult
from pc_manager_agent.platform_support.base import CancellationSignal


class SystemOptimizationPlatform(Protocol):
    """Expose observations only; no method on this interface can mutate Windows."""

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
        """Return a bounded point-in-time system snapshot."""
        ...

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
        """Inspect metadata within exact known or explicitly authorized roots."""
        ...
