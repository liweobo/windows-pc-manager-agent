from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    ObservationAvailability,
    OptimizationEvidence,
    OwnershipConfidence,
    ScanScopeDecision,
    StorageObservation,
)
from pc_manager_agent.safety.cleanup_candidate_policy import CleanupCandidatePolicy


@pytest.mark.performance
def test_ten_thousand_candidate_classifications_remain_bounded() -> None:
    observation = StorageObservation(
        category=CleanupCategory.USER_TEMP,
        source="benchmark",
        observed_size_bytes=1024,
        item_count=1,
        newest_modified_at=datetime(2020, 1, 1, tzinfo=UTC),
        availability=ObservationAvailability.AVAILABLE,
        scope_decision=ScanScopeDecision.METADATA_ONLY,
        ownership_confidence=OwnershipConfidence.HIGH,
        evidence=(OptimizationEvidence.DIRECT_FILE_METADATA,),
    )
    policy = CleanupCandidatePolicy()
    started = time.perf_counter()
    results = tuple(policy.classify(observation, inactive_days=90) for _ in range(10_000))
    duration = time.perf_counter() - started
    assert len(results) == 10_000
    assert duration < 10.0
