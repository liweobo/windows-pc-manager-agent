"""Staged duplicate-content detection with cancellation and identity checks."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from pc_manager_agent.domain.errors import FileAnalysisError, ScanCancelledError
from pc_manager_agent.domain.file_analysis import (
    DuplicateFileAnalysisRequest,
    DuplicateFileAnalysisResult,
    DuplicateGroup,
    StoredFileRecord,
)
from pc_manager_agent.domain.reports import ScanIssue
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.tools.file_tools.hashing import SafeFileHasher
from pc_manager_agent.tools.file_tools.large_file_analyzer import AnalyzerProgress
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class DuplicateFileAnalyzer:
    """Verify duplicate candidates without selecting an original or a copy."""

    def __init__(
        self,
        repository: AnalysisResultRepository,
        hasher: SafeFileHasher,
        *,
        progress_callback: AnalyzerProgress | None = None,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._progress_callback = progress_callback
        self._manifest = ToolManifest(
            name="file.analyze.duplicates",
            description="Verify duplicate content using staged read-only hashes",
            input_model=DuplicateFileAnalysisRequest,
            output_model=DuplicateFileAnalysisResult,
            risk_level=RiskLevel.R0,
            required_permissions=("current-user-read",),
            read_only=True,
            idempotent=True,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("scan session exists", "paths remain authorized"),
            postconditions=("no user file is modified",),
            timeout_seconds=3_600,
            max_batch_size=100_000,
            audit_fields=("analysis_session_id", "quick_hash_bytes", "byte_verify"),
            supported_platforms=("windows",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Run staged hashing and return only verified duplicate groups."""
        if not isinstance(request, DuplicateFileAnalysisRequest):
            raise TypeError("DuplicateFileAnalyzer received an unexpected input model")
        return self.analyze(request, cancellation)

    def analyze(
        self,
        request: DuplicateFileAnalysisRequest,
        cancellation: CancellationToken,
    ) -> DuplicateFileAnalysisResult:
        """Group by size, quick hash, SHA-256, and optional byte comparison."""
        sizes = self._repository.duplicate_sizes(request.analysis_session_id)
        groups: list[DuplicateGroup] = []
        issues: list[ScanIssue] = []
        cancelled = False
        for index, size_bytes in enumerate(sizes, start=1):
            if cancellation.cancellation_requested():
                cancelled = True
                break
            records = self._repository.records_by_size(request.analysis_session_id, size_bytes)
            try:
                verified = self._analyze_same_size(
                    records,
                    cancellation,
                    sample_bytes=request.quick_hash_bytes,
                    byte_verify=request.byte_verify,
                    issues=issues,
                )
            except ScanCancelledError:
                cancelled = True
                break
            for sha256, equal_records, byte_verified in verified:
                group_id = sha256[:16]
                self._repository.mark_duplicate(
                    [record.record_id for record in equal_records], group_id
                )
                groups.append(
                    DuplicateGroup(
                        group_id=group_id,
                        size_bytes=size_bytes,
                        sha256=sha256,
                        files=tuple(record.metadata for record in equal_records),
                        byte_verified=byte_verified,
                    )
                )
            if self._progress_callback is not None:
                self._progress_callback(
                    request.analysis_session_id,
                    "duplicates",
                    index,
                    len(sizes),
                )
        if issues:
            self._repository.store_issues(request.analysis_session_id, issues)
        files = sum(len(group.files) for group in groups)
        reclaimable = sum(group.size_bytes * (len(group.files) - 1) for group in groups)
        return DuplicateFileAnalysisResult(
            analysis_session_id=request.analysis_session_id,
            groups=tuple(groups),
            files=files,
            reclaimable_bytes=reclaimable,
            issues=tuple(issues),
            cancelled=cancelled,
        )

    def _analyze_same_size(
        self,
        records: tuple[StoredFileRecord, ...],
        cancellation: CancellationToken,
        *,
        sample_bytes: int,
        byte_verify: bool,
        issues: list[ScanIssue],
    ) -> list[tuple[str, tuple[StoredFileRecord, ...], bool]]:
        quick_groups: defaultdict[str, list[StoredFileRecord]] = defaultdict(list)
        for record in records:
            fingerprint = self._safe_hash(
                record,
                cancellation,
                issues,
                quick=True,
                sample_bytes=sample_bytes,
            )
            if fingerprint is not None:
                quick_groups[fingerprint].append(record)

        full_groups: defaultdict[str, list[StoredFileRecord]] = defaultdict(list)
        for candidates in quick_groups.values():
            if len(candidates) < 2:
                continue
            for record in candidates:
                digest = self._safe_hash(
                    record,
                    cancellation,
                    issues,
                    quick=False,
                    sample_bytes=sample_bytes,
                )
                if digest is not None:
                    full_groups[digest].append(record)

        verified: list[tuple[str, tuple[StoredFileRecord, ...], bool]] = []
        for digest, candidates in full_groups.items():
            if len(candidates) < 2:
                continue
            if not byte_verify:
                verified.append((digest, tuple(candidates), False))
                continue
            equal = [candidates[0]]
            for candidate in candidates[1:]:
                try:
                    if self._hasher.byte_equal(candidates[0], candidate, cancellation):
                        equal.append(candidate)
                except ScanCancelledError:
                    raise
                except FileAnalysisError as exc:
                    issues.append(
                        ScanIssue(
                            code="byte-verification-error",
                            message=str(exc),
                            path=candidate.metadata.path,
                        )
                    )
            if len(equal) >= 2:
                verified.append((digest, tuple(equal), True))
        return verified

    def _safe_hash(
        self,
        record: StoredFileRecord,
        cancellation: CancellationToken,
        issues: list[ScanIssue],
        *,
        quick: bool,
        sample_bytes: int,
    ) -> str | None:
        try:
            if quick:
                return self._hasher.quick_hash(
                    record,
                    cancellation,
                    sample_bytes=sample_bytes,
                )
            return self._hasher.sha256(record, cancellation)
        except ScanCancelledError:
            raise
        except (FileAnalysisError, OSError) as exc:
            issues.append(
                ScanIssue(
                    code="hashing-error",
                    message=str(exc),
                    path=record.metadata.path,
                )
            )
            return None
