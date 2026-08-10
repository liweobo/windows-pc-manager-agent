"""Deterministic large-file analysis over paged metadata."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from pc_manager_agent.domain.file_analysis import (
    LargeFileAnalysisRequest,
    LargeFileAnalysisResult,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest

AnalyzerProgress = Callable[[UUID, str, int, int | None], None]


class LargeFileAnalyzer:
    """Mark threshold candidates and build size, type, and directory aggregates."""

    def __init__(
        self,
        repository: AnalysisResultRepository,
        *,
        progress_callback: AnalyzerProgress | None = None,
    ) -> None:
        self._repository = repository
        self._progress_callback = progress_callback
        self._manifest = ToolManifest(
            name="file.analyze.large",
            description="Analyze stored file metadata against a confirmed size threshold",
            input_model=LargeFileAnalysisRequest,
            output_model=LargeFileAnalysisResult,
            risk_level=RiskLevel.R0,
            required_permissions=("application-result-read",),
            read_only=True,
            idempotent=True,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("scan session exists",),
            postconditions=("no user file is modified",),
            timeout_seconds=3_600,
            max_batch_size=100_000,
            audit_fields=("analysis_session_id", "minimum_size_bytes"),
            supported_platforms=("windows",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Analyze paged metadata and return typed aggregates."""
        if not isinstance(request, LargeFileAnalysisRequest):
            raise TypeError("LargeFileAnalyzer received an unexpected input model")
        return self.analyze(request, cancellation)

    def analyze(
        self,
        request: LargeFileAnalysisRequest,
        cancellation: CancellationToken,
    ) -> LargeFileAnalysisResult:
        """Mark files at or above the inclusive configured threshold."""
        total_records = self._repository.record_count(request.analysis_session_id)
        processed = 0
        files = 0
        total_bytes = 0
        by_extension: Counter[str] = Counter()
        by_directory: Counter[str] = Counter()
        by_size_band: Counter[str] = Counter()
        cancelled = False
        for batch in self._repository.iter_records(request.analysis_session_id):
            if cancellation.cancellation_requested():
                cancelled = True
                break
            matching_ids: list[int] = []
            for record in batch:
                processed += 1
                metadata = record.metadata
                if metadata.size_bytes < request.minimum_size_bytes:
                    continue
                matching_ids.append(record.record_id)
                files += 1
                total_bytes += metadata.size_bytes
                by_extension[metadata.extension or "(none)"] += metadata.size_bytes
                by_directory[str(Path(metadata.path).parent)] += metadata.size_bytes
                by_size_band[self._size_band(metadata.size_bytes)] += metadata.size_bytes
            self._repository.mark_large(matching_ids)
            if self._progress_callback is not None:
                self._progress_callback(
                    request.analysis_session_id,
                    "large_files",
                    processed,
                    total_records,
                )
        return LargeFileAnalysisResult(
            analysis_session_id=request.analysis_session_id,
            files=files,
            total_bytes=total_bytes,
            by_extension=dict(by_extension),
            by_directory=dict(by_directory),
            by_size_band=dict(by_size_band),
            cancelled=cancelled,
        )

    @staticmethod
    def _size_band(size_bytes: int) -> str:
        gib = 1_073_741_824
        if size_bytes < gib:
            return "under_1_gib"
        if size_bytes < 5 * gib:
            return "1_to_5_gib"
        if size_bytes < 10 * gib:
            return "5_to_10_gib"
        return "10_gib_and_above"
