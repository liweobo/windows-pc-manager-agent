"""Read-only bridges from verified Stage 1 and Stage 4D3 reports."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.file_analysis import StoredFileRecord
from pc_manager_agent.domain.software_residuals import (
    OwnershipConfidence as ResidualOwnershipConfidence,
)
from pc_manager_agent.domain.software_residuals import (
    ResidualCandidate,
    ResidualClassification,
    UserDataProtectionLevel,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    CleanupReasonCode,
    CleanupSafetyClassification,
    ObservationAvailability,
    OptimizationConfidence,
    OptimizationEvidence,
    OwnershipConfidence,
    ProtectionLevel,
    ScanScopeDecision,
    StorageAnalysisResult,
    StorageObservation,
)
from pc_manager_agent.persistence.analysis_results import (
    AnalysisResultRepository,
    AnalysisResultStoreError,
)
from pc_manager_agent.persistence.software_residuals import (
    SoftwareResidualRepository,
    SoftwareResidualStoreError,
)
from pc_manager_agent.platform_support.base import CancellationSignal


class OptimizationEvidenceSource(Protocol):
    """Read-only bridge for current identity-matching prior analysis evidence."""

    def observations(
        self,
        authorized_roots: tuple[Path, ...],
        *,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> StorageAnalysisResult:
        """Return bounded report evidence without granting execution authority."""
        ...


class RepositoryOptimizationEvidenceSource:
    """Convert current, identity-stable historical analysis evidence into observations."""

    def __init__(
        self,
        stage1: AnalysisResultRepository,
        residuals: SoftwareResidualRepository,
    ) -> None:
        self._stage1 = stage1
        self._residuals = residuals

    def observations(
        self,
        authorized_roots: tuple[Path, ...],
        *,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> StorageAnalysisResult:
        """Return Fresh identity-matching Stage 1 and Stage 4D3 observations.

        Repository errors fail soft into a partial source. Historical evidence is never
        an execution permission, and stale objects are skipped rather than refreshed by name.
        """
        observations: list[StorageObservation] = []
        partial: list[str] = []
        skipped: list[str] = []
        remaining = max_items
        try:
            records = self._stage1.recent_matching_candidates(
                authorized_roots, limit=min(remaining, 25_000)
            )
        except AnalysisResultStoreError:
            records = ()
            partial.append("stage1-analysis-history")
        for record in records:
            if cancellation.cancellation_requested() or remaining <= 0:
                skipped.append("stage1-analysis-history")
                break
            observation = self._stage1_observation(record, authorized_roots)
            if observation is not None:
                observations.append(observation)
                remaining -= 1
        if remaining > 0 and not cancellation.cancellation_requested():
            try:
                contexts = self._residuals.list_eligible_contexts(limit=100)
            except SoftwareResidualStoreError:
                contexts = ()
                partial.append("stage4d3-residual-history")
            for context in contexts:
                if cancellation.cancellation_requested() or remaining <= 0:
                    skipped.append("stage4d3-residual-history")
                    break
                try:
                    report = self._residuals.latest_report(context.context_id)
                except SoftwareResidualStoreError:
                    partial.append("stage4d3-residual-history")
                    continue
                if report is None or report.deletion_performed:
                    continue
                for candidate in report.candidates:
                    if cancellation.cancellation_requested() or remaining <= 0:
                        skipped.append("stage4d3-residual-history")
                        break
                    observation = self._residual_observation(candidate)
                    if observation is not None:
                        observations.append(observation)
                        remaining -= 1
        return StorageAnalysisResult(
            observations=tuple(observations),
            partial_sources=tuple(dict.fromkeys(partial)),
            skipped_sources=tuple(dict.fromkeys(skipped)),
            truncated=remaining <= 0,
        )

    @staticmethod
    def _stage1_observation(
        record: StoredFileRecord, authorized_roots: tuple[Path, ...]
    ) -> StorageObservation | None:
        metadata = record.metadata
        path = metadata.path.absolute()
        if not any(_within(path, root.absolute()) for root in authorized_roots):
            return None
        try:
            current = path.lstat()
        except OSError:
            return None
        if not stat.S_ISREG(current.st_mode) or path.is_symlink():
            return None
        if metadata.file_id is None or metadata.device_id is None:
            return None
        if (
            int(current.st_ino) != metadata.file_id
            or int(current.st_dev) != metadata.device_id
            or int(current.st_size) != metadata.size_bytes
        ):
            return None
        if record.duplicate_group_id is not None:
            category = CleanupCategory.DUPLICATE_FILE
            evidence = OptimizationEvidence.STAGE1_VERIFIED_DUPLICATE_REPORT
            reason = CleanupReasonCode.VERIFIED_DUPLICATE_GROUP
        elif record.inactive is not None:
            category = CleanupCategory.INACTIVE_LARGE_FILE
            evidence = OptimizationEvidence.AUTHORIZED_STAGE1_SCOPE
            reason = CleanupReasonCode.POSSIBLY_INACTIVE
        elif record.is_large:
            category = CleanupCategory.LARGE_FILE
            evidence = OptimizationEvidence.AUTHORIZED_STAGE1_SCOPE
            reason = CleanupReasonCode.USER_AUTHORIZED_LARGE_FILE
        else:
            return None
        return StorageObservation(
            category=category,
            source="stage1-current-identity-report",
            path=path,
            observed_size_bytes=metadata.size_bytes,
            item_count=1,
            oldest_modified_at=metadata.modified_at,
            newest_modified_at=metadata.modified_at,
            availability=ObservationAvailability.AVAILABLE,
            scope_decision=ScanScopeDecision.AUTHORIZED_USER_PATH,
            ownership_confidence=OwnershipConfidence.HIGH,
            evidence=(evidence, OptimizationEvidence.DIRECT_FILE_METADATA),
            source_safety_classification=CleanupSafetyClassification.CAUTION,
            source_protection_level=ProtectionLevel.CAUTION,
            source_confidence=OptimizationConfidence.HIGH,
            source_reason_codes=(reason,),
        )

    @staticmethod
    def _residual_observation(candidate: ResidualCandidate) -> StorageObservation | None:
        path = candidate.path.absolute()
        try:
            current = path.lstat()
        except OSError:
            return None
        identity = candidate.identity
        if path.is_symlink() or candidate.reparse_or_symlink:
            return None
        if (
            int(current.st_dev) != identity.device_id
            or int(current.st_ino) != identity.file_id
            or int(current.st_size) != identity.size_bytes
            or int(current.st_mtime_ns) != identity.modified_time_ns
        ):
            return None
        category = _residual_category(candidate.classification)
        protection = _residual_protection(candidate.protection_level)
        ownership = _residual_ownership(candidate.ownership_confidence)
        confidence = OptimizationConfidence(ownership.value)
        ordinary = category in {
            CleanupCategory.PROGRAM_RESIDUAL,
            CleanupCategory.APPLICATION_CACHE,
            CleanupCategory.LOG,
            CleanupCategory.USER_TEMP,
            CleanupCategory.CRASH_DUMP,
        }
        safety = (
            CleanupSafetyClassification.CAUTION
            if ordinary and protection in {ProtectionLevel.NONE, ProtectionLevel.CAUTION}
            else CleanupSafetyClassification.PROTECTED
        )
        reason = (
            CleanupReasonCode.EXACT_UNINSTALL_CONTEXT
            if safety is CleanupSafetyClassification.CAUTION
            else CleanupReasonCode.USER_DATA_MAY_BE_PRESENT
        )
        return StorageObservation(
            category=category,
            source="stage4d3-current-identity-report",
            path=path,
            observed_size_bytes=candidate.size_bytes,
            item_count=1,
            oldest_modified_at=candidate.modified_at,
            newest_modified_at=candidate.modified_at,
            availability=ObservationAvailability.AVAILABLE,
            scope_decision=ScanScopeDecision.METADATA_ONLY,
            ownership_confidence=ownership,
            evidence=(
                OptimizationEvidence.STAGE4D3_EXACT_RESIDUAL_REPORT,
                OptimizationEvidence.DIRECT_FILE_METADATA,
            ),
            source_safety_classification=safety,
            source_protection_level=protection,
            source_confidence=confidence,
            source_reason_codes=(reason,),
        )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _residual_category(value: ResidualClassification) -> CleanupCategory:
    return {
        ResidualClassification.PROGRAM_RESIDUAL: CleanupCategory.PROGRAM_RESIDUAL,
        ResidualClassification.CACHE: CleanupCategory.APPLICATION_CACHE,
        ResidualClassification.LOG: CleanupCategory.LOG,
        ResidualClassification.TEMPORARY_DATA: CleanupCategory.USER_TEMP,
        ResidualClassification.CRASH_DUMP: CleanupCategory.CRASH_DUMP,
    }.get(value, CleanupCategory.UNKNOWN)


def _residual_protection(value: UserDataProtectionLevel) -> ProtectionLevel:
    return {
        UserDataProtectionLevel.NONE: ProtectionLevel.NONE,
        UserDataProtectionLevel.CAUTION: ProtectionLevel.CAUTION,
        UserDataProtectionLevel.PROTECTED: ProtectionLevel.PROTECTED,
        UserDataProtectionLevel.STRONGLY_PROTECTED: ProtectionLevel.STRONGLY_PROTECTED,
        UserDataProtectionLevel.UNKNOWN: ProtectionLevel.UNKNOWN,
    }[value]


def _residual_ownership(value: ResidualOwnershipConfidence) -> OwnershipConfidence:
    return OwnershipConfidence(value.value.upper())
