from __future__ import annotations

import pytest

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticPlan,
    DiagnosticThresholds,
    SuggestedAction,
    SuggestedActionType,
    SystemCollector,
)


def test_diagnostic_plan_digest_changes_with_executable_parameters() -> None:
    plan = DiagnosticPlan(
        summary="performance",
        user_goal="diagnose performance",
        intent=DiagnosticIntent.PERFORMANCE,
        collectors=(SystemCollector.SYSTEM_INFO, SystemCollector.CPU),
    )
    changed = plan.model_copy(update={"sample_count": 4})
    assert plan.canonical_digest() != changed.canonical_digest()


def test_diagnostic_plan_rejects_duplicates_and_non_r0_contract() -> None:
    base = {
        "summary": "overview",
        "user_goal": "overview",
        "intent": DiagnosticIntent.OVERVIEW,
        "collectors": (SystemCollector.SYSTEM_INFO,),
    }
    with pytest.raises(ValueError, match="read-only R0"):
        DiagnosticPlan(**base, risk_level=RiskLevel.R1)
    with pytest.raises(ValueError, match="unique"):
        DiagnosticPlan(
            **{
                **base,
                "collectors": (SystemCollector.SYSTEM_INFO, SystemCollector.SYSTEM_INFO),
            }
        )


def test_thresholds_and_suggestions_are_bounded_and_non_executable() -> None:
    with pytest.raises(ValueError):
        DiagnosticThresholds(cpu_notice_percent=101)
    with pytest.raises(ValueError, match="cannot execute"):
        SuggestedAction(
            action_type=SuggestedActionType.REVIEW,
            title="terminate",
            description="not allowed",
            executable=True,
        )
