"""Cautious inactive-file evidence analysis without value judgements."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from pc_manager_agent.domain.file_analysis import (
    InactiveAssessment,
    InactiveConfidence,
    InactiveFileAnalysisRequest,
    InactiveFileAnalysisResult,
)
from pc_manager_agent.domain.reports import FileMetadata
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.tools.file_tools.large_file_analyzer import AnalyzerProgress
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest

AtimeReliabilityProbe = Callable[[Path], bool | None]


class InactiveFileAnalyzer:
    """Identify only evidence-supported, possibly inactive file candidates."""

    def __init__(
        self,
        repository: AnalysisResultRepository,
        *,
        atime_reliability: AtimeReliabilityProbe | None = None,
        now: Callable[[], datetime] | None = None,
        progress_callback: AnalyzerProgress | None = None,
    ) -> None:
        self._repository = repository
        self._atime_reliability = atime_reliability or (lambda _root: None)
        self._now = now or (lambda: datetime.now(UTC))
        self._progress_callback = progress_callback
        self._manifest = ToolManifest(
            name="file.analyze.inactive",
            description="Assess possibly inactive files from timestamp evidence",
            input_model=InactiveFileAnalysisRequest,
            output_model=InactiveFileAnalysisResult,
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
            audit_fields=("analysis_session_id", "inactive_days"),
            supported_platforms=("windows",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Analyze paged metadata and return confidence aggregates."""
        if not isinstance(request, InactiveFileAnalysisRequest):
            raise TypeError("InactiveFileAnalyzer received an unexpected input model")
        return self.analyze(request, cancellation)

    def analyze(
        self,
        request: InactiveFileAnalysisRequest,
        cancellation: CancellationToken,
    ) -> InactiveFileAnalysisResult:
        """Mark candidates only when access and modification evidence are old."""
        threshold = self._now() - timedelta(days=request.inactive_days)
        total_records = self._repository.record_count(request.analysis_session_id)
        reliability_cache: dict[Path, bool | None] = {}
        by_confidence: Counter[InactiveConfidence] = Counter()
        processed = 0
        files = 0
        total_bytes = 0
        cancelled = False
        for batch in self._repository.iter_records(request.analysis_session_id):
            if cancellation.cancellation_requested():
                cancelled = True
                break
            for record in batch:
                processed += 1
                root = record.metadata.scan_root
                if root not in reliability_cache:
                    reliability_cache[root] = self._atime_reliability(root)
                assessment = self.assess(
                    record.metadata,
                    threshold=threshold,
                    inactive_days=request.inactive_days,
                    atime_reliable=reliability_cache[root],
                )
                if not assessment.possibly_inactive:
                    continue
                self._repository.mark_inactive(record.record_id, assessment)
                files += 1
                total_bytes += record.metadata.size_bytes
                by_confidence[assessment.confidence] += 1
            if self._progress_callback is not None:
                self._progress_callback(
                    request.analysis_session_id,
                    "inactive_files",
                    processed,
                    total_records,
                )
        return InactiveFileAnalysisResult(
            analysis_session_id=request.analysis_session_id,
            files=files,
            total_bytes=total_bytes,
            by_confidence=dict(by_confidence),
            cancelled=cancelled,
        )

    @staticmethod
    def assess(
        metadata: FileMetadata,
        *,
        threshold: datetime,
        inactive_days: int,
        atime_reliable: bool | None,
    ) -> InactiveAssessment:
        """Build a conservative assessment from typed timestamp attributes."""
        accessed_at = metadata.accessed_at
        modified_at = metadata.modified_at
        created_at = metadata.created_at
        old_access = accessed_at < threshold
        old_modified = modified_at < threshold
        old_created = created_at < threshold
        if not (old_access and old_modified):
            return InactiveAssessment(
                possibly_inactive=False,
                confidence=InactiveConfidence.UNKNOWN,
                evidence=("timestamps do not jointly meet the inactive threshold",),
                threshold_days=inactive_days,
            )
        evidence = [
            "last access time is older than the configured threshold",
            "last modified time is older than the configured threshold",
        ]
        if old_created:
            evidence.append("creation time is also older than the configured threshold")
        if atime_reliable is True:
            confidence = InactiveConfidence.HIGH if old_created else InactiveConfidence.MEDIUM
            evidence.append("Windows reports last-access updates as enabled")
        elif atime_reliable is False:
            confidence = InactiveConfidence.LOW
            evidence.append("last-access updates may be disabled or deferred")
        else:
            confidence = InactiveConfidence.MEDIUM if old_created else InactiveConfidence.LOW
            evidence.append("last-access reliability could not be confirmed")
        return InactiveAssessment(
            possibly_inactive=True,
            confidence=confidence,
            evidence=tuple(evidence),
            threshold_days=inactive_days,
        )
