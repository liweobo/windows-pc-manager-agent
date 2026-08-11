from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.unit.test_models import build_plan

from pc_manager_agent.domain.plans import PlanStep, TaskScope
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReviewer
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest
from pc_manager_agent.tools.registry import ToolRegistry


def scanner_reviewer(root: Path) -> SafetyReviewer:
    policy = PathPolicy.for_scan_root(root)
    registry = ToolRegistry()
    registry.register(DirectoryScannerTool(policy))
    return SafetyReviewer(registry, policy)


@pytest.mark.security
def test_valid_scan_plan_is_approved(tmp_path: Path) -> None:
    plan = build_plan(tmp_path).model_copy(
        update={
            "steps": (
                build_plan(tmp_path)
                .steps[0]
                .model_copy(
                    update={
                        "arguments": {
                            "root": str(tmp_path),
                            "max_files": 10,
                            "timeout_seconds": 1,
                        }
                    }
                ),
            )
        }
    )
    assert scanner_reviewer(tmp_path).review(plan).approved


@pytest.mark.security
def test_unknown_tool_invalid_arguments_risk_and_rollback_are_denied(tmp_path: Path) -> None:
    policy = PathPolicy.for_scan_root(tmp_path)
    empty = SafetyReviewer(ToolRegistry(), policy)
    unknown = empty.review(build_plan(tmp_path))
    assert {issue.code for issue in unknown.issues} == {"unknown-tool"}

    registry = ToolRegistry()
    registry.register(DirectoryScannerTool(policy))
    reviewer = SafetyReviewer(registry, policy)
    base = build_plan(tmp_path)
    bad_step = base.steps[0].model_copy(
        update={
            "arguments": {"root": str(tmp_path), "unexpected": True},
            "risk_level": RiskLevel.R1,
            "rollback_level": RollbackLevel.FULL,
        }
    )
    review = reviewer.review(base.model_copy(update={"steps": (bad_step,)}))
    codes = {issue.code for issue in review.issues}
    assert {"invalid-arguments", "risk-mismatch", "rollback-mismatch"} <= codes


@pytest.mark.security
def test_scope_expansion_and_missing_plan_confirmation_are_denied(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    reviewer = scanner_reviewer(root)
    plan = build_plan(root)
    step = plan.steps[0].model_copy(
        update={"arguments": {"root": str(outside), "max_files": 1, "timeout_seconds": 1}}
    )
    review = reviewer.review(
        plan.model_copy(update={"steps": (step,), "requires_plan_confirmation": False})
    )
    assert {"scope-expansion", "plan-confirmation-required"} <= {
        issue.code for issue in review.issues
    }


@pytest.mark.security
def test_invalid_included_scope_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    reviewer = scanner_reviewer(root)
    plan = build_plan(root).model_copy(update={"scope": TaskScope(included_paths=(outside,))})
    review = reviewer.review(plan)
    assert "invalid-scope" in {issue.code for issue in review.issues}


class EmptyModel(BaseModel):
    pass


class HighRiskTool:
    def __init__(self, risk: RiskLevel) -> None:
        self._manifest = ToolManifest(
            name="system.high-risk",
            description="test denial",
            input_model=EmptyModel,
            output_model=EmptyModel,
            risk_level=risk,
            required_permissions=("administrator",),
            read_only=False,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.MANUAL,
            preconditions=(),
            postconditions=(),
            timeout_seconds=1,
            max_batch_size=1,
            audit_fields=(),
            supported_platforms=("windows",),
            supports_preview=True,
        )

    @property
    def manifest(self) -> ToolManifest:
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        return EmptyModel()


@pytest.mark.security
@pytest.mark.parametrize("risk", [RiskLevel.R3, RiskLevel.R4])
def test_r3_and_r4_are_denied(tmp_path: Path, risk: RiskLevel) -> None:
    policy = PathPolicy.for_scan_root(tmp_path)
    registry = ToolRegistry()
    registry.register(HighRiskTool(risk))
    step = PlanStep(
        step_id="step-high",
        tool_name="system.high-risk",
        description="denied",
        arguments={},
        risk_level=risk,
        requires_confirmation=False,
        rollback_level=RollbackLevel.NONE,
    )
    base = build_plan(tmp_path)
    plan = base.model_copy(update={"steps": (step,)})
    review = SafetyReviewer(registry, policy).review(plan)
    codes = {issue.code for issue in review.issues}
    assert "mvp-risk-denied" in codes
    assert "runtime-confirmation-required" in codes
