"""Invariant and risk tests for Stage 4D4 cleanup models."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.residual_cleanup_policy import CleanupRiskPolicy


def test_cleanup_request_accepts_only_report_and_unique_candidate_references() -> None:
    report_id = uuid4()
    candidate_id = uuid4()
    request = ResidualCleanupRequest(
        source_report_id=report_id,
        selected_residual_ids=(candidate_id,),
    )
    assert request.source_report_id == report_id
    assert request.selected_residual_ids == (candidate_id,)
    with pytest.raises(ValidationError, match="unique"):
        ResidualCleanupRequest(
            source_report_id=report_id,
            selected_residual_ids=(candidate_id, candidate_id),
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ResidualCleanupRequest.model_validate(
            {
                "source_report_id": str(report_id),
                "selected_residual_ids": [str(candidate_id)],
                "path": "C:/untrusted/model/supplied/path",
                "action": "PERMANENT_DELETE",
            }
        )


@pytest.mark.parametrize(
    ("impact", "expected"),
    (
        (
            {"item_count": 5, "object_count": 100, "total_size": 1_000, "largest_item": 500},
            RiskLevel.R2,
        ),
        (
            {"item_count": 6, "object_count": 100, "total_size": 1_000, "largest_item": 500},
            RiskLevel.R2_HIGH_IMPACT,
        ),
        (
            {"item_count": 5, "object_count": 101, "total_size": 1_000, "largest_item": 500},
            RiskLevel.R2_HIGH_IMPACT,
        ),
        (
            {"item_count": 5, "object_count": 100, "total_size": 1_001, "largest_item": 500},
            RiskLevel.R2_HIGH_IMPACT,
        ),
        (
            {"item_count": 5, "object_count": 100, "total_size": 1_000, "largest_item": 501},
            RiskLevel.R2_HIGH_IMPACT,
        ),
    ),
)
def test_cleanup_risk_is_promoted_by_any_high_impact_threshold(
    impact: dict[str, int],
    expected: RiskLevel,
) -> None:
    policy = CleanupRiskPolicy(
        max_normal_item_count=5,
        max_normal_object_count=100,
        max_normal_total_size=1_000,
        max_normal_single_item_size=500,
    )
    assert policy.classify(**impact) is expected
