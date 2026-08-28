from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupReasonCode,
    CleanupSafetyClassification,
    ObservationAvailability,
    OptimizationConfidence,
    OptimizationEvidence,
    OptimizationPlan,
    OptimizationToolName,
    OwnershipConfidence,
    ProtectionLevel,
    ScanScopeDecision,
    StorageObservation,
)
from pc_manager_agent.safety.cleanup_candidate_policy import CleanupCandidatePolicy


def test_plan_uses_canonical_tool_subset_and_is_never_executable() -> None:
    plan = OptimizationPlan(
        user_goal="health check",
        summary="read only",
        goals=("GENERAL_HEALTH_CHECK",),
        tools=tuple(OptimizationToolName),
    )
    assert plan.estimated_system_changes == 0
    assert plan.stage4e1_executable is False
    invalid = tuple(reversed(tuple(OptimizationToolName)))
    with pytest.raises(ValidationError, match="canonical allow-list subset"):
        plan.model_copy(update={"tools": invalid}).model_validate(
            plan.model_copy(update={"tools": invalid}).model_dump()
        )


def test_protected_candidate_cannot_claim_reclaimable_bytes() -> None:
    with pytest.raises(ValidationError, match="cannot be reclaimable"):
        CleanupCandidate(
            category=CleanupCategory.INSTALLER_CACHE_CANDIDATE,
            source="installer",
            observed_size_bytes=100,
            potential_reclaim_bytes=100,
            item_count=1,
            ownership_confidence=OwnershipConfidence.UNKNOWN,
            safety_classification=CleanupSafetyClassification.PROTECTED,
            protection_level=ProtectionLevel.SYSTEM_PROTECTED,
            recoverability=RollbackLevel.NONE,
            confidence=OptimizationConfidence.UNKNOWN,
            evidence=(OptimizationEvidence.PROTECTION_POLICY,),
            reason_codes=(CleanupReasonCode.SYSTEM_MANAGED,),
        )


def test_candidate_policy_separates_old_cache_from_system_managed_data() -> None:
    policy = CleanupCandidatePolicy()
    old_cache = StorageObservation(
        category=CleanupCategory.APPLICATION_CACHE,
        source="cache",
        observed_size_bytes=100,
        item_count=2,
        newest_modified_at=datetime(2025, 1, 1, tzinfo=UTC),
        availability=ObservationAvailability.AVAILABLE,
        scope_decision=ScanScopeDecision.METADATA_ONLY,
        ownership_confidence=OwnershipConfidence.HIGH,
        evidence=(OptimizationEvidence.DIRECT_FILE_METADATA,),
    )
    candidate = policy.classify(old_cache, inactive_days=90, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert candidate.safety_classification is CleanupSafetyClassification.LOW_RISK_CANDIDATE
    assert candidate.potential_reclaim_bytes == 100

    update = old_cache.model_copy(
        update={
            "category": CleanupCategory.WINDOWS_UPDATE_CANDIDATE,
            "availability": ObservationAvailability.UNAVAILABLE,
        }
    )
    protected = policy.classify(update, inactive_days=90)
    assert protected.potential_reclaim_bytes is None
    assert protected.stage4e1_executable is False


@pytest.mark.parametrize(
    ("category", "availability", "expected"),
    (
        (
            CleanupCategory.INSTALLER_CACHE_CANDIDATE,
            ObservationAvailability.AVAILABLE,
            CleanupSafetyClassification.PROTECTED,
        ),
        (
            CleanupCategory.DELIVERY_OPTIMIZATION_CACHE,
            ObservationAvailability.AVAILABLE,
            CleanupSafetyClassification.PROTECTED,
        ),
        (
            CleanupCategory.RECYCLE_BIN_CONTENT,
            ObservationAvailability.AVAILABLE,
            CleanupSafetyClassification.HIGH_IMPACT,
        ),
        (
            CleanupCategory.DUPLICATE_FILE,
            ObservationAvailability.AVAILABLE,
            CleanupSafetyClassification.CAUTION,
        ),
        (
            CleanupCategory.PROGRAM_RESIDUAL,
            ObservationAvailability.AVAILABLE,
            CleanupSafetyClassification.CAUTION,
        ),
        (
            CleanupCategory.LOG,
            ObservationAvailability.PARTIAL,
            CleanupSafetyClassification.CAUTION,
        ),
    ),
)
def test_candidate_policy_covers_protected_and_caution_branches(
    category: CleanupCategory,
    availability: ObservationAvailability,
    expected: CleanupSafetyClassification,
) -> None:
    evidence = [OptimizationEvidence.DIRECT_FILE_METADATA]
    if category is CleanupCategory.DUPLICATE_FILE:
        evidence.append(OptimizationEvidence.STAGE1_VERIFIED_DUPLICATE_REPORT)
    if category is CleanupCategory.PROGRAM_RESIDUAL:
        evidence.append(OptimizationEvidence.STAGE4D3_EXACT_RESIDUAL_REPORT)
    observation = StorageObservation(
        category=category,
        source="test",
        observed_size_bytes=100,
        item_count=1,
        newest_modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        availability=availability,
        scope_decision=ScanScopeDecision.METADATA_ONLY,
        ownership_confidence=OwnershipConfidence.HIGH,
        evidence=tuple(evidence),
    )
    candidate = CleanupCandidatePolicy().classify(
        observation, inactive_days=90, now=datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert candidate.safety_classification is expected
    assert candidate.stage4e1_executable is False


def test_executable_candidate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="never executable"):
        CleanupCandidate(
            category=CleanupCategory.LARGE_FILE,
            source="test",
            observed_size_bytes=100,
            item_count=1,
            ownership_confidence=OwnershipConfidence.HIGH,
            safety_classification=CleanupSafetyClassification.CAUTION,
            protection_level=ProtectionLevel.CAUTION,
            recoverability=RollbackLevel.MANUAL,
            confidence=OptimizationConfidence.MEDIUM,
            evidence=(OptimizationEvidence.DIRECT_FILE_METADATA,),
            reason_codes=(CleanupReasonCode.USER_AUTHORIZED_LARGE_FILE,),
            stage4e1_executable=True,
        )
