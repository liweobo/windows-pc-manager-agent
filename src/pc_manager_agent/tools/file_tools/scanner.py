"""Metadata-only directory scanner with conservative Windows path handling."""

from __future__ import annotations

import mimetypes
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from pc_manager_agent.domain.reports import (
    FileMetadata,
    ScanIssue,
    ScanReport,
    ScanRequest,
    ScanSummary,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class DirectoryScannerTool:
    """Enumerate metadata inside one approved directory without following links."""

    def __init__(
        self,
        path_policy: PathPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path_policy = path_policy
        self._clock = clock
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
        issues: list[ScanIssue] = []
        directories_seen = 0
        total_size = 0
        cancelled = False
        timed_out = False
        truncated = False
        stack = [(root, self._directory_identity(root))]
        excluded = tuple(
            Path(os.path.abspath(os.path.normpath(path))) for path in request.excluded_paths
        )

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
                issues.append(
                    ScanIssue(
                        code=rejection, message="Directory changed or is unsafe", path=directory
                    )
                )
                continue
            try:
                if self._directory_identity(directory) != expected_identity:
                    issues.append(
                        ScanIssue(
                            code="path-identity-changed",
                            message="Directory identity changed after discovery",
                            path=directory,
                        )
                    )
                    continue
            except OSError as exc:
                issues.append(self._os_issue(directory, exc))
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
                            issues.append(
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
                                issues.append(
                                    ScanIssue(
                                        code="unsupported-entry",
                                        message="Entry is not a regular file or directory",
                                        path=path,
                                    )
                                )
                                continue
                            metadata = entry.stat(follow_symlinks=False)
                        except OSError as exc:
                            issues.append(self._os_issue(path, exc))
                            continue
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
                        )
                        files.append(file_metadata)
                        total_size += metadata.st_size
                        if len(files) >= request.max_files:
                            truncated = True
                            break
                    if cancelled or timed_out or truncated:
                        break
            except OSError as exc:
                issues.append(self._os_issue(directory, exc))

        duration_ms = max(0, round((self._clock() - started) * 1_000))
        summary = ScanSummary(
            files_seen=len(files),
            directories_seen=directories_seen,
            total_size_bytes=total_size,
            issues=len(issues),
            cancelled=cancelled,
            timed_out=timed_out,
            truncated=truncated,
            duration_ms=duration_ms,
        )
        return ScanReport(root=root, files=tuple(files), issues=tuple(issues), summary=summary)

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
