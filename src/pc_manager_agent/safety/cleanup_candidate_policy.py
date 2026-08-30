"""Deterministic cleanup-candidate classification for read-only Stage 4E1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupReasonCode,
    CleanupSafetyClassification,
    ObservationAvailability,
    OptimizationConfidence,
    OptimizationEvidence,
    ProtectionLevel,
    StorageObservation,
)

_LOW_RISK_METADATA_CATEGORIES = {
    CleanupCategory.USER_TEMP,
    CleanupCategory.SYSTEM_TEMP,
    CleanupCategory.APPLICATION_CACHE,
    CleanupCategory.BROWSER_CACHE,
    CleanupCategory.LOG,
    CleanupCategory.CRASH_DUMP,
}
_REASON_BY_CATEGORY = {
    CleanupCategory.USER_TEMP: CleanupReasonCode.KNOWN_TEMP_LOCATION,
    CleanupCategory.SYSTEM_TEMP: CleanupReasonCode.KNOWN_TEMP_LOCATION,
    CleanupCategory.APPLICATION_CACHE: CleanupReasonCode.KNOWN_CACHE_LOCATION,
    CleanupCategory.BROWSER_CACHE: CleanupReasonCode.KNOWN_CACHE_LOCATION,
    CleanupCategory.LOG: CleanupReasonCode.KNOWN_LOG_LOCATION,
    CleanupCategory.CRASH_DUMP: CleanupReasonCode.KNOWN_DUMP_LOCATION,
    CleanupCategory.RECYCLE_BIN_CONTENT: CleanupReasonCode.RECYCLE_BIN_SUMMARY,
    CleanupCategory.LARGE_FILE: CleanupReasonCode.USER_AUTHORIZED_LARGE_FILE,
    CleanupCategory.INACTIVE_LARGE_FILE: CleanupReasonCode.POSSIBLY_INACTIVE,
    CleanupCategory.DUPLICATE_FILE: CleanupReasonCode.VERIFIED_DUPLICATE_GROUP,
    CleanupCategory.PROGRAM_RESIDUAL: CleanupReasonCode.EXACT_UNINSTALL_CONTEXT,
    CleanupCategory.OBSOLETE_SHORTCUT: CleanupReasonCode.EXACT_UNINSTALL_CONTEXT,
}


class CleanupCandidatePolicy:
    """Convert raw observations into cautious, non-executable report candidates."""

    def classify(
        self,
        observation: StorageObservation,
        *,
        inactive_days: int,
        now: datetime | None = None,
    ) -> CleanupCandidate:
        """Classify one observation without inferring deletion safety from its name."""
        current = now or datetime.now(UTC)
        reason = _REASON_BY_CATEGORY.get(observation.category, CleanupReasonCode.UNKNOWN_OWNERSHIP)
        if observation.availability is ObservationAvailability.UNAVAILABLE:
            return self._blocked(
                observation,
                CleanupSafetyClassification.UNKNOWN,
                ProtectionLevel.UNKNOWN,
                CleanupReasonCode.RELIABLE_SIZE_UNAVAILABLE,
            )
        if (
            observation.source_safety_classification is not None
            and observation.source_protection_level is not None
            and observation.source_confidence is not None
        ):
            safety = observation.source_safety_classification
            protected = safety in {
                CleanupSafetyClassification.PROTECTED,
                CleanupSafetyClassification.BLOCKED,
                CleanupSafetyClassification.UNKNOWN,
            }
            return CleanupCandidate(
                category=observation.category,
                source=observation.source,
                path=observation.path,
                observed_size_bytes=observation.observed_size_bytes,
                potential_reclaim_bytes=None,
                item_count=observation.item_count,
                ownership_confidence=observation.ownership_confidence,
                safety_classification=safety,
                protection_level=observation.source_protection_level,
                recoverability=RollbackLevel.NONE if protected else RollbackLevel.MANUAL,
                confidence=observation.source_confidence,
                evidence=observation.evidence,
                reason_codes=observation.source_reason_codes
                or (CleanupReasonCode.EXACT_UNINSTALL_CONTEXT,),
                future_admin_requirement=None,
                source_reference=observation.source_reference,
            )
        if observation.category is CleanupCategory.INSTALLER_CACHE_CANDIDATE:
            return self._blocked(
                observation,
                CleanupSafetyClassification.PROTECTED,
                ProtectionLevel.SYSTEM_PROTECTED,
                CleanupReasonCode.PROTECTED_COMPONENT_STORE,
            )
        if observation.category in {
            CleanupCategory.WINDOWS_UPDATE_CANDIDATE,
            CleanupCategory.DELIVERY_OPTIMIZATION_CACHE,
            CleanupCategory.UNKNOWN,
        }:
            return self._blocked(
                observation,
                CleanupSafetyClassification.PROTECTED,
                ProtectionLevel.SYSTEM_PROTECTED,
                CleanupReasonCode.SYSTEM_MANAGED,
            )
        if observation.category is CleanupCategory.RECYCLE_BIN_CONTENT:
            return self._candidate(
                observation,
                safety=CleanupSafetyClassification.HIGH_IMPACT,
                protection=ProtectionLevel.CAUTION,
                confidence=OptimizationConfidence.HIGH,
                reason=reason,
                reclaim=observation.observed_size_bytes,
                recoverability=RollbackLevel.MANUAL,
            )
        if observation.category in {
            CleanupCategory.LARGE_FILE,
            CleanupCategory.INACTIVE_LARGE_FILE,
            CleanupCategory.DUPLICATE_FILE,
        }:
            return self._candidate(
                observation,
                safety=CleanupSafetyClassification.CAUTION,
                protection=ProtectionLevel.CAUTION,
                confidence=(
                    OptimizationConfidence.HIGH
                    if OptimizationEvidence.STAGE1_VERIFIED_DUPLICATE_REPORT in observation.evidence
                    else OptimizationConfidence.MEDIUM
                ),
                reason=reason,
                reclaim=None,
                recoverability=RollbackLevel.MANUAL,
            )
        if observation.category in {
            CleanupCategory.PROGRAM_RESIDUAL,
            CleanupCategory.OBSOLETE_SHORTCUT,
        }:
            return self._candidate(
                observation,
                safety=CleanupSafetyClassification.CAUTION,
                protection=ProtectionLevel.CAUTION,
                confidence=(
                    OptimizationConfidence.HIGH
                    if OptimizationEvidence.STAGE4D3_EXACT_RESIDUAL_REPORT in observation.evidence
                    else OptimizationConfidence.UNKNOWN
                ),
                reason=reason,
                reclaim=None,
                recoverability=RollbackLevel.MANUAL,
            )
        old_enough = (
            observation.newest_modified_at is not None
            and observation.newest_modified_at <= current - timedelta(days=inactive_days)
        )
        if observation.category in _LOW_RISK_METADATA_CATEGORIES and old_enough:
            return self._candidate(
                observation,
                safety=CleanupSafetyClassification.LOW_RISK_CANDIDATE,
                protection=ProtectionLevel.CAUTION,
                confidence=OptimizationConfidence.MEDIUM,
                reason=reason,
                reclaim=observation.observed_size_bytes,
                recoverability=RollbackLevel.MANUAL,
            )
        return self._candidate(
            observation,
            safety=CleanupSafetyClassification.CAUTION,
            protection=ProtectionLevel.CAUTION,
            confidence=OptimizationConfidence.LOW,
            reason=CleanupReasonCode.RECENT_ACTIVITY,
            reclaim=None,
            recoverability=RollbackLevel.MANUAL,
        )

    @staticmethod
    def _blocked(
        observation: StorageObservation,
        safety: CleanupSafetyClassification,
        protection: ProtectionLevel,
        reason: CleanupReasonCode,
    ) -> CleanupCandidate:
        evidence = tuple(
            dict.fromkeys((*observation.evidence, OptimizationEvidence.PROTECTION_POLICY))
        )
        return CleanupCandidate(
            category=observation.category,
            source=observation.source,
            path=observation.path,
            observed_size_bytes=observation.observed_size_bytes,
            item_count=observation.item_count,
            ownership_confidence=observation.ownership_confidence,
            safety_classification=safety,
            protection_level=protection,
            recoverability=RollbackLevel.NONE,
            confidence=OptimizationConfidence.UNKNOWN,
            evidence=evidence,
            reason_codes=(reason,),
            future_admin_requirement=None,
            source_reference=observation.source_reference,
        )

    @staticmethod
    def _candidate(
        observation: StorageObservation,
        *,
        safety: CleanupSafetyClassification,
        protection: ProtectionLevel,
        confidence: OptimizationConfidence,
        reason: CleanupReasonCode,
        reclaim: int | None,
        recoverability: RollbackLevel,
    ) -> CleanupCandidate:
        return CleanupCandidate(
            category=observation.category,
            source=observation.source,
            path=observation.path,
            observed_size_bytes=observation.observed_size_bytes,
            potential_reclaim_bytes=reclaim,
            item_count=observation.item_count,
            ownership_confidence=observation.ownership_confidence,
            safety_classification=safety,
            protection_level=protection,
            recoverability=recoverability,
            confidence=confidence,
            evidence=observation.evidence,
            reason_codes=(reason,),
            future_admin_requirement=False,
            source_reference=observation.source_reference,
        )
