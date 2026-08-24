"""Shared bounded metadata traversal for exact Stage 4D3 roots."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    ResidualAnalysisStatus,
    ResidualCandidate,
    ResidualClassification,
    ResidualIdentity,
    ResidualIssue,
    ResidualObjectType,
    ResidualRecommendation,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import (
    ResidualScanScopePolicy,
    ResidualScopeError,
)
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(slots=True)
class ResidualCollectionBudget:
    """Shared cooperative object/time budget across every collector."""

    max_objects: int
    timeout_seconds: float
    cancellation: CancellationToken
    clock: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    objects_seen: int = 0
    stop_status: ResidualAnalysisStatus | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.max_objects <= 25_000:
            raise ValueError("Residual object budget must be between 1 and 25,000")
        if not 0 < self.timeout_seconds <= 600:
            raise ValueError("Residual timeout must be between 0 and 600 seconds")
        self.started = self.clock()

    def consume(self) -> bool:
        """Consume one object slot or set a truthful terminal stop reason."""
        if not self.can_continue():
            return False
        if self.objects_seen >= self.max_objects:
            self.stop_status = ResidualAnalysisStatus.TRUNCATED
            return False
        self.objects_seen += 1
        return True

    def can_continue(self) -> bool:
        """Check cancellation and timeout dynamically for long-running scans."""
        if self.stop_status is not None:
            return False
        if self.cancellation.cancellation_requested():
            self.stop_status = ResidualAnalysisStatus.CANCELLED
            return False
        if self.clock() - self.started >= self.timeout_seconds:
            self.stop_status = ResidualAnalysisStatus.TIMED_OUT
            return False
        return True


@dataclass(frozen=True, slots=True)
class CollectorResult:
    """One collector's bounded candidate and issue output."""

    candidates: tuple[ResidualCandidate, ...] = ()
    issues: tuple[ResidualIssue, ...] = ()
    root_scanned: bool = False


class ResidualCollector(Protocol):
    """Protocol implemented by each finite source-specific collector."""

    def supports(self, source: ResidualSource) -> bool:
        """Return whether this collector owns a source type."""
        ...

    def collect(
        self,
        evidence: ContextPathEvidence,
        report_id: UUID,
        budget: ResidualCollectionBudget,
        *,
        uninstall_verified: bool,
    ) -> CollectorResult:
        """Collect metadata below one exact root without following redirects."""
        ...


class SourceResidualCollector:
    """Reusable exact-root traversal configured for a finite source set."""

    def __init__(
        self,
        sources: frozenset[ResidualSource],
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        self._sources = sources
        self._scope = scope
        self._classifier = classifier
        self._ownership = ownership
        self._protection = protection

    def supports(self, source: ResidualSource) -> bool:
        """Return whether this collector handles the exact context source."""
        return source in self._sources

    def collect(
        self,
        evidence: ContextPathEvidence,
        report_id: UUID,
        budget: ResidualCollectionBudget,
        *,
        uninstall_verified: bool,
    ) -> CollectorResult:
        """Traverse one exact root with identity, reparse, depth, and budget checks."""
        root = evidence.path
        try:
            root_metadata = os.lstat(root)
        except FileNotFoundError:
            return CollectorResult(
                issues=(
                    ResidualIssue(
                        code="path-not-present",
                        message="Exact pre-uninstall path is no longer present.",
                        path=root,
                    ),
                )
            )
        except PermissionError:
            return CollectorResult(
                issues=(
                    ResidualIssue(
                        code="access-denied",
                        message="Access to exact residual root was denied.",
                        path=root,
                    ),
                )
            )
        except OSError:
            return CollectorResult(
                issues=(
                    ResidualIssue(
                        code="filesystem-error",
                        message="Exact residual root could not be inspected.",
                        path=root,
                    ),
                )
            )
        try:
            root = self._scope.validate_existing_root(root)
        except ResidualScopeError as exc:
            return CollectorResult(
                issues=(
                    ResidualIssue(
                        code="unsafe-root",
                        message=str(exc),
                        path=evidence.path,
                    ),
                )
            )
        candidates: list[ResidualCandidate] = []
        issues: list[ResidualIssue] = []
        if stat.S_ISREG(root_metadata.st_mode):
            try:
                current_root = os.lstat(root)
            except OSError:
                return CollectorResult(
                    issues=(
                        ResidualIssue(
                            code="path-identity-changed",
                            message="File identity could not be revalidated before reporting.",
                            path=root,
                        ),
                    ),
                    root_scanned=True,
                )
            if (current_root.st_dev, current_root.st_ino) != (
                root_metadata.st_dev,
                root_metadata.st_ino,
            ):
                return CollectorResult(
                    issues=(
                        ResidualIssue(
                            code="path-identity-changed",
                            message="File identity changed after discovery.",
                            path=root,
                        ),
                    ),
                    root_scanned=True,
                )
            if budget.consume():
                candidates.append(
                    self._candidate(
                        report_id,
                        root,
                        root,
                        evidence,
                        current_root,
                        uninstall_verified=uninstall_verified,
                    )
                )
            return CollectorResult(tuple(candidates), tuple(issues), True)
        if not stat.S_ISDIR(root_metadata.st_mode):
            issues.append(
                ResidualIssue(
                    code="unsupported-root-type",
                    message="Exact path is not a regular file or directory.",
                    path=root,
                )
            )
            return CollectorResult(issues=tuple(issues), root_scanned=True)

        stack: list[tuple[Path, int, tuple[int, int]]] = [
            (root, 0, (root_metadata.st_dev, root_metadata.st_ino))
        ]
        while stack and budget.can_continue():
            directory, depth, identity = stack.pop()
            try:
                current = os.stat(directory, follow_symlinks=False)
            except OSError:
                self._record_issue(
                    issues,
                    ResidualIssue(
                        code="directory-unavailable",
                        message="Directory became unavailable during analysis.",
                        path=directory,
                    ),
                )
                continue
            if (current.st_dev, current.st_ino) != identity:
                self._record_issue(
                    issues,
                    ResidualIssue(
                        code="path-identity-changed",
                        message="Directory identity changed after discovery.",
                        path=directory,
                    ),
                )
                continue
            if budget.consume():
                candidates.append(
                    self._candidate(
                        report_id,
                        directory,
                        root,
                        evidence,
                        current,
                        uninstall_verified=uninstall_verified,
                    )
                )
            else:
                break
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not budget.can_continue():
                            break
                        path = Path(entry.path)
                        rejection = self._scope.entry_rejection_reason(path, root)
                        if rejection == "reparse-point":
                            try:
                                metadata = os.lstat(path)
                            except OSError:
                                metadata = None
                            if metadata is not None and budget.consume():
                                candidates.append(
                                    self._candidate(
                                        report_id,
                                        path,
                                        root,
                                        evidence,
                                        metadata,
                                        uninstall_verified=uninstall_verified,
                                        force_reparse=True,
                                    )
                                )
                            self._record_issue(
                                issues,
                                ResidualIssue(
                                    code="reparse-point-skipped",
                                    message="Link, junction, or reparse point was not followed.",
                                    path=path,
                                ),
                            )
                            continue
                        if rejection is not None:
                            self._record_issue(
                                issues,
                                ResidualIssue(
                                    code=rejection,
                                    message="Entry was skipped by residual scope policy.",
                                    path=path,
                                ),
                            )
                            continue
                        try:
                            # Windows DirEntry.stat may expose zero device/file IDs; lstat
                            # provides the stable identity required for later revalidation.
                            metadata = os.lstat(path)
                            if entry.is_dir(follow_symlinks=False):
                                if depth >= evidence.max_depth:
                                    self._record_issue(
                                        issues,
                                        ResidualIssue(
                                            code="depth-limit",
                                            message=(
                                                "Directory was not traversed beyond the "
                                                "configured depth."
                                            ),
                                            path=path,
                                        ),
                                    )
                                    continue
                                stack.append((path, depth + 1, (metadata.st_dev, metadata.st_ino)))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                self._record_issue(
                                    issues,
                                    ResidualIssue(
                                        code="unsupported-entry",
                                        message="Entry is not a regular file or directory.",
                                        path=path,
                                    ),
                                )
                                continue
                        except PermissionError:
                            self._record_issue(
                                issues,
                                ResidualIssue(
                                    code="access-denied",
                                    message="Entry metadata could not be read.",
                                    path=path,
                                ),
                            )
                            continue
                        except OSError:
                            self._record_issue(
                                issues,
                                ResidualIssue(
                                    code="filesystem-error",
                                    message="Entry metadata could not be read.",
                                    path=path,
                                ),
                            )
                            continue
                        if not budget.consume():
                            break
                        candidates.append(
                            self._candidate(
                                report_id,
                                path,
                                root,
                                evidence,
                                metadata,
                                uninstall_verified=uninstall_verified,
                            )
                        )
            except PermissionError:
                self._record_issue(
                    issues,
                    ResidualIssue(
                        code="access-denied",
                        message="Directory enumeration was denied.",
                        path=directory,
                    ),
                )
            except OSError:
                self._record_issue(
                    issues,
                    ResidualIssue(
                        code="filesystem-error",
                        message="Directory enumeration failed.",
                        path=directory,
                    ),
                )
        return CollectorResult(tuple(candidates), tuple(issues), True)

    def _candidate(
        self,
        report_id: UUID,
        path: Path,
        root: Path,
        evidence: ContextPathEvidence,
        metadata: os.stat_result,
        *,
        uninstall_verified: bool,
        force_reparse: bool = False,
    ) -> ResidualCandidate:
        reparse = force_reparse
        if not reparse:
            attributes = getattr(metadata, "st_file_attributes", 0)
            reparse = bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        classification, classification_reasons = self._classifier.classify(
            path, evidence.source, evidence.expected_classification
        )
        if reparse:
            classification = ResidualClassification.UNKNOWN
            classification_reasons = ("reparse-target-not-read",)
        shared = evidence.shared_location or any(
            part.casefold() in {"common", "shared", "public"} for part in path.parts
        )
        confidence, ownership_evidence = self._ownership.evaluate(evidence, shared_location=shared)
        protection, protection_reasons = self._protection.protect(path, classification)
        flags: list[str] = []
        if reparse:
            flags.append("reparse-point-not-followed")
        if shared:
            flags.append("possibly-shared-location")
        if not uninstall_verified:
            flags.append("uninstall-not-fully-verified")
        modified = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
        if modified >= datetime.now(UTC) - timedelta(days=7):
            flags.append("recently-modified")
        if (
            protection
            in {
                UserDataProtectionLevel.PROTECTED,
                UserDataProtectionLevel.STRONGLY_PROTECTED,
                UserDataProtectionLevel.UNKNOWN,
            }
            or reparse
        ):
            recommendation = ResidualRecommendation.PROTECT
        elif shared:
            recommendation = ResidualRecommendation.REVIEW_MANUALLY
        else:
            recommendation = ResidualRecommendation.REPORT
        mode = metadata.st_mode
        object_type = ResidualObjectType.OTHER
        if reparse:
            object_type = ResidualObjectType.REPARSE_POINT
        elif stat.S_ISDIR(mode):
            object_type = ResidualObjectType.DIRECTORY
        elif path.suffix.casefold() == ".lnk":
            object_type = ResidualObjectType.SHORTCUT
        elif stat.S_ISREG(mode):
            object_type = ResidualObjectType.FILE
        return ResidualCandidate(
            report_id=report_id,
            identity=ResidualIdentity(
                normalized_path=Path(os.path.abspath(os.fspath(path))),
                device_id=metadata.st_dev,
                file_id=metadata.st_ino,
                object_type=object_type,
                size_bytes=metadata.st_size if stat.S_ISREG(mode) else 0,
                modified_time_ns=metadata.st_mtime_ns,
            ),
            path=path,
            scan_root=root,
            source=evidence.source,
            object_type=object_type,
            size_bytes=metadata.st_size if stat.S_ISREG(mode) else 0,
            created_at=datetime.fromtimestamp(metadata.st_ctime, tz=UTC),
            modified_at=modified,
            accessed_at=datetime.fromtimestamp(metadata.st_atime, tz=UTC),
            classification=classification,
            classification_reasons=classification_reasons,
            ownership_confidence=confidence,
            ownership_evidence=ownership_evidence,
            protection_level=protection,
            protection_reasons=protection_reasons,
            risk_flags=tuple(flags),
            readable=True,
            reparse_or_symlink=reparse,
            recommendation=recommendation,
        )

    @staticmethod
    def _record_issue(issues: list[ResidualIssue], issue: ResidualIssue) -> None:
        if len(issues) < 5_000:
            issues.append(issue)
