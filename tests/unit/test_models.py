from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.plans import EstimatedImpact, PlanStep, TaskPlan, TaskScope
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


def build_plan(root: Path) -> TaskPlan:
    step = PlanStep(
        step_id="step-scan",
        tool_name="file.scan",
        description="scan",
        arguments={"root": str(root)},
        risk_level=RiskLevel.R0,
        requires_confirmation=False,
        rollback_level=RollbackLevel.NONE,
    )
    return TaskPlan(
        summary="safe scan",
        user_goal="scan metadata",
        scope=TaskScope(included_paths=(root,)),
        steps=(step,),
        estimated_impact=EstimatedImpact(),
    )


def test_plan_digest_is_stable_and_changes_with_content(tmp_path: Path) -> None:
    plan = build_plan(tmp_path)
    assert plan.canonical_digest() == plan.canonical_digest()
    changed = plan.model_copy(update={"summary": "changed"})
    assert changed.canonical_digest() != plan.canonical_digest()


def test_arguments_digest_is_key_order_independent(tmp_path: Path) -> None:
    first = build_plan(tmp_path).steps[0]
    second = first.model_copy(update={"arguments": {"z": 1, "root": str(tmp_path)}})
    third = first.model_copy(update={"arguments": {"root": str(tmp_path), "z": 1}})
    assert second.arguments_digest() == third.arguments_digest()


def test_plan_rejects_empty_scope_and_steps(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        TaskScope(included_paths=())
    plan = build_plan(tmp_path)
    with pytest.raises(ValidationError):
        TaskPlan(
            summary=plan.summary,
            user_goal=plan.user_goal,
            scope=plan.scope,
            steps=(),
        )


def test_plan_rejects_duplicate_step_ids(tmp_path: Path) -> None:
    plan = build_plan(tmp_path)
    with pytest.raises(ValidationError, match="unique"):
        plan.model_copy(update={"steps": (plan.steps[0], plan.steps[0])}, deep=True).model_validate(
            {
                **plan.model_dump(),
                "steps": [plan.steps[0].model_dump(), plan.steps[0].model_dump()],
            }
        )


def test_risk_severity_and_rollback_values() -> None:
    assert RiskLevel.R0.severity == 0
    assert RiskLevel.R4.severity == 4
    assert RollbackLevel.MANUAL.value == "MANUAL"
