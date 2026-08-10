"""Metadata-only directory scanner with conservative Windows path handling."""

from __future__ import annotations

import mimetypes
import os
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from pc_manager_agent.domain.reports import (
    FileMetadata,
    ScanBatch,
    ScanIssue,
    ScanProgress,
    ScanReport,
    ScanRequest,
    ScanStatus,
    ScanSummary,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.classifier import FileTypeClassifier
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest

BatchConsumer = Callable[[ScanBatch], None]
IssueConsumer = Callable[[UUID, tuple[ScanIssue, ...]], None]
ProgressCallback = Callable[[ScanProgress], None]


class DirectoryScannerTool:
    """Enumerate metadata inside one approved directory without following links."""

    def __init__(
        self,
        path_policy: PathPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        classifier: FileTypeClassifier | None = None,
        batch_consumer: BatchConsumer | None = None,
        issue_consumer: IssueConsumer | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._path_policy = path_policy
        self._clock = clock
        self._classifier = classifier or FileTypeClassifier()
        self._batch_consumer = batch_consumer
        self._issue_consumer = issue_consumer
        self._progress_callback = progress_callback
        self._manifest = ToolManifest(
            name="file.scan",
            description="Read file metadata inside one explicitly approved root",
            input_model=ScanRequest,
            output_model=ScanReport,
            risk_level=RiskLevel.R0,
            required_permissions=("current-user-read",),
            read_only=True,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("root exists", "root is approved", "audit store is available"),
            postconditions=("no file content or metadata is modified",),
            timeout_seconds=3_600,
            max_batch_size=100_000,
            audit_fields=("root", "excluded_paths", "max_files", "timeout_seconds"),
            supported_platforms=("windows",),
            scope_argument_names=("root",),
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the scanner's immutable security manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate the root and return a bounded scan report."""
        if not isinstance(request, ScanRequest):
            msg = "DirectoryScannerTool received an unexpected input model"
            raise TypeError(msg)
        return self._scan(request, cancellation)

    def _scan(self, request: ScanRequest, cancellation: CancellationToken) -> ScanReport:
        root = self._path_policy.validate_scan_root(request.root)
        started = self._clock()
        files: list[FileMetadata] = []
        batch: list[FileMetadata] = []
        issues: list[ScanIssue] = []
        issue_batch: list[ScanIssue] = []
        issue_count = 0
        files_seen = 0
        directories_seen = 0
        total_size = 0
        cancelled = False
        timed_out = False
        truncated = False
        stack = [(root, self._directory_identity(root))]
        excluded = self._validated_exclusions(request.excluded_paths, root)

        def record_issue(issue: ScanIssue) -> None:
            nonlocal issue_count
            issue_count += 1
            issue_batch.append(issue)
            if request.retain_files:
                issues.append(issue)
            if len(issue_batch) >= request.batch_size:
                self._flush_issues(request, issue_batch)

        while stack:
            if cancellation.cancellation_requested():
                cancelled = True
                break
            if self._clock() - started >= request.timeout_seconds:
                timed_out = True
                break
            directory, expected_identity = stack.pop()
            directories_seen += 1
            rejection = self._path_policy.entry_rejection_reason(directory)
            if rejection:
                record_issue(
                    ScanIssue(
                        code=rejection, message="Directory changed or is unsafe", path=directory
                    )
                )
                continue
            try:
                if self._directory_identity(directory) != expected_identity:
                    record_issue(
                        ScanIssue(
                            code="path-identity-changed",
                            message="Directory identity changed after discovery",
                            path=directory,
                        )
                    )
                    continue
            except OSError as exc:
                record_issue(self._os_issue(directory, exc))
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancellation.cancellation_requested():
                            cancelled = True
                            break
                        if self._clock() - started >= request.timeout_seconds:
                            timed_out = True
                            break
                        path = Path(entry.path)
                        if any(
                            self._path_is_within(path, excluded_root) for excluded_root in excluded
                        ):
                            continue
                        rejection = self._path_policy.entry_rejection_reason(path)
                        if rejection:
                            record_issue(
                                ScanIssue(
                                    code=rejection,
                                    message="Path skipped by safety policy",
                                    path=path,
                                )
                            )
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                # Use the same OS API at discovery and revalidation. On
                                # Windows, DirEntry.stat and os.stat can expose different
                                # identifiers for the same directory.
                                stack.append((path, self._directory_identity(path)))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                record_issue(
                                    ScanIssue(
                                        code="unsupported-entry",
                                        message="Entry is not a regular file or directory",
                                        path=path,
                                    )
                                )
                                continue
                            metadata = entry.stat(follow_symlinks=False)
                        except OSError as exc:
                            record_issue(self._os_issue(path, exc))
                            continue
                        attributes = getattr(metadata, "st_file_attributes", 0)
                        hidden_flag = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
                        readonly_flag = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)
                        system_flag = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
                        offline_flag = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
                        file_metadata = FileMetadata(
                            path=path,
                            name=entry.name,
                            extension=path.suffix.casefold(),
                            media_type=mimetypes.guess_type(entry.name)[0],
                            size_bytes=metadata.st_size,
                            created_at=datetime.fromtimestamp(metadata.st_ctime, tz=UTC),
                            modified_at=datetime.fromtimestamp(metadata.st_mtime, tz=UTC),
                            accessed_at=datetime.fromtimestamp(metadata.st_atime, tz=UTC),
                            scan_root=root,
                            hidden=entry.name.startswith(".") or bool(attributes & hidden_flag),
                            read_only=bool(attributes & readonly_flag)
                            or not bool(metadata.st_mode & stat.S_IWUSR),
                            system=bool(attributes & system_flag),
                            offline=bool(attributes & offline_flag),
                            category=self._classifier.classify(path),
                            # Windows directory enumeration can report zero for these
                            # identifiers even when an opened handle later exposes real
                            # values. Zero is therefore "unknown", not a stable identity.
                            file_id=metadata.st_ino or None,
                            device_id=metadata.st_dev or None,
                            windows_attributes=max(0, attributes),
                        )
                        files_seen += 1
                        if request.retain_files:
                            files.append(file_metadata)
                        batch.append(file_metadata)
                        total_size += metadata.st_size
                        if len(batch) >= request.batch_size:
                            self._flush_batch(request, batch)
                            self._flush_issues(request, issue_batch)
                            self._emit_progress(
                                request,
                                files_seen,
                                directories_seen,
                                total_size,
                                issue_count,
                            )
                        if files_seen >= request.max_files:
                            truncated = True
                            break
                    if cancelled or timed_out or truncated:
                        break
            except OSError as exc:
                record_issue(self._os_issue(directory, exc))

        self._flush_batch(request, batch)
        self._flush_issues(request, issue_batch)
        self._emit_progress(
            request,
            files_seen,
            directories_seen,
            total_size,
            issue_count,
        )

        duration_ms = max(0, round((self._clock() - started) * 1_000))
        status = ScanStatus.COMPLETED
        if cancelled:
            status = ScanStatus.CANCELLED
        elif timed_out:
            status = ScanStatus.TIMED_OUT
        elif truncated:
            status = ScanStatus.TRUNCATED
        summary = ScanSummary(
            files_seen=files_seen,
            directories_seen=directories_seen,
            total_size_bytes=total_size,
            issues=issue_count,
            cancelled=cancelled,
            timed_out=timed_out,
            truncated=truncated,
            duration_ms=duration_ms,
            status=status,
        )
        return ScanReport(
            root=root,
            files=tuple(files),
            issues=tuple(issues),
            summary=summary,
            session_id=request.session_id,
        )

    def _validated_exclusions(self, paths: tuple[Path, ...], root: Path) -> tuple[Path, ...]:
        exclusions: list[Path] = []
        for path in paths:
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Excluded path is not an absolute safe path: {path}")
            candidate = Path(os.path.abspath(os.path.normpath(path)))
            if not self._path_is_within(candidate, root):
                raise ValueError(f"Excluded path is outside the scan root: {candidate}")
            exclusions.append(candidate)
        return tuple(exclusions)

    def _flush_batch(self, request: ScanRequest, batch: list[FileMetadata]) -> None:
        if not batch:
            return
        if self._batch_consumer is not None and request.session_id is not None:
            self._batch_consumer(ScanBatch(session_id=request.session_id, files=tuple(batch)))
        batch.clear()

    def _flush_issues(self, request: ScanRequest, issues: list[ScanIssue]) -> None:
        if not issues:
            return
        if self._issue_consumer is not None and request.session_id is not None:
            self._issue_consumer(request.session_id, tuple(issues))
        issues.clear()

    def _emit_progress(
        self,
        request: ScanRequest,
        files_seen: int,
        directories_seen: int,
        total_size: int,
        issue_count: int,
    ) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            ScanProgress(
                session_id=request.session_id,
                files_seen=files_seen,
                directories_seen=directories_seen,
                total_size_bytes=total_size,
                issues=issue_count,
            )
        )

    @staticmethod
    def _path_is_within(path: Path, root: Path) -> bool:
        candidate = os.path.normcase(os.path.abspath(path))
        boundary = os.path.normcase(os.path.abspath(root))
        try:
            return os.path.commonpath((candidate, boundary)) == boundary
        except ValueError:
            return False

    @staticmethod
    def _os_issue(path: Path, error: OSError) -> ScanIssue:
        code = "permission-denied" if isinstance(error, PermissionError) else "filesystem-error"
        return ScanIssue(code=code, message=str(error), path=path)

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        """Return a stable Windows volume/file identifier without following links."""
        metadata = os.stat(path, follow_symlinks=False)
        return metadata.st_dev, metadata.st_ino
