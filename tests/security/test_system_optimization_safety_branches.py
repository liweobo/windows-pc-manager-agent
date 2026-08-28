from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from tests.fixtures.system_optimization import build_optimization_registry

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import SystemCollector
from pc_manager_agent.domain.system_optimization import (
    OptimizationGoal,
    OptimizationPlan,
    OptimizationToolName,
)
from pc_manager_agent.safety.system_optimization import SystemOptimizationSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry


def _plan() -> OptimizationPlan:
    return OptimizationPlan(
        user_goal="health",
        summary="read only",
        goals=(OptimizationGoal.GENERAL_HEALTH_CHECK,),
        tools=tuple(OptimizationToolName),
    )


def test_empty_registry_reports_boundary_and_unknown_tools(runtime: ApplicationRuntime) -> None:
    review = SystemOptimizationSafetyValidator(ToolRegistry(), runtime.authorized_paths).review(
        _plan()
    )
    codes = {item.code for item in review.issues}
    assert "registry-boundary" in codes
    assert "unknown-tool" in codes


def test_defensive_review_rejects_invalid_constructed_plan(runtime: ApplicationRuntime) -> None:
    valid = _plan()
    invalid = OptimizationPlan.model_construct(
        **{
            **valid.__dict__,
            "tools": tuple(reversed(tuple(OptimizationToolName))),
            "snapshot_collectors": tuple(reversed(tuple(SystemCollector))),
            "risk_level": RiskLevel.R2,
            "read_only": False,
            "rollback_level": RollbackLevel.MANUAL,
            "requires_runtime_confirmation": True,
            "estimated_system_changes": 1,
            "stage4e1_executable": True,
        }
    )
    review = SystemOptimizationSafetyValidator(
        build_optimization_registry(), runtime.authorized_paths
    ).review(invalid)
    codes = {item.code for item in review.issues}
    assert {"tool-order", "collector-scope", "risk", "execution-boundary"} <= codes


def test_unsafe_manifest_is_rejected(runtime: ApplicationRuntime) -> None:
    class UnsafeRegistry:
        names = tuple(sorted(item.value for item in OptimizationToolName))

        def manifest(self, _name: str) -> object:
            return SimpleNamespace(
                risk_level=RiskLevel.R2,
                read_only=False,
                requires_runtime_confirmation=True,
                rollback_level=RollbackLevel.MANUAL,
            )

    validator = SystemOptimizationSafetyValidator(  # type: ignore[arg-type]
        UnsafeRegistry(), runtime.authorized_paths
    )
    review = validator.review(_plan())
    assert {item.code for item in review.issues} == {"unsafe-manifest"}


def test_stale_authorized_identity_is_rejected(runtime: ApplicationRuntime, tmp_path: Path) -> None:
    valid = _plan()
    stale = OptimizationPlan.model_construct(
        **{
            **valid.__dict__,
            "authorized_root_ids": (uuid4(),),
            "authorized_roots": (tmp_path,),
        }
    )
    review = SystemOptimizationSafetyValidator(
        build_optimization_registry(), runtime.authorized_paths
    ).review(stale)
    assert "authorized-scope" in {item.code for item in review.issues}
