from __future__ import annotations

from pathlib import Path

from tests.stage4a_support import FakeProcessPlatform, process_observation

from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionType,
    ProcessTargetQuery,
    ProcessTargetQueryType,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import ProcessActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.process_actions import RequestProcessExitTool


def _plan() -> ProcessActionPlan:
    observation = process_observation()
    return ProcessActionPlan(
        user_goal="close demo",
        summary="close",
        action=ProcessActionType.REQUEST_GRACEFUL_EXIT,
        target_query=ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=4_001),
        targets=(
            ResolvedProcessTarget(
                display_name="demo",
                application_group_key="a" * 64,
                members=(observation,),
            ),
        ),
        risk_level=RiskLevel.R2,
        estimated_processes_affected=1,
    )


def _preview(plan: ProcessActionPlan):
    return ProcessPreviewEngine(
        ProcessSafetyPolicy(
            current_owner_sid="S-1-5-21-1000",
            current_session_id=1,
            windows_directory=Path("C:/Windows"),
        )
    ).build(plan)


def test_validator_rejects_unknown_tool_and_changed_preview() -> None:
    plan = _plan()
    preview = _preview(plan)
    missing = ProcessActionSafetyValidator(ToolRegistry()).review(plan, preview)
    assert not missing.approved
    assert "not registered" in missing.issues[0]

    registry = ToolRegistry()
    registry.register(RequestProcessExitTool(FakeProcessPlatform(plan.targets[0].members)))
    changed = preview.model_copy(update={"plan_digest": "f" * 64})
    review = ProcessActionSafetyValidator(registry).review(plan, changed)
    assert not review.approved
    assert any("stale" in issue for issue in review.issues)


def test_validator_rejects_blocked_policy_and_configured_batch_limit() -> None:
    plan = _plan()
    platform = FakeProcessPlatform(plan.targets[0].members)
    registry = ToolRegistry()
    registry.register(RequestProcessExitTool(platform))
    blocked_preview = ProcessPreviewEngine(
        ProcessSafetyPolicy(
            current_owner_sid="S-1-5-21-9999",
            current_session_id=1,
            windows_directory=Path("C:/Windows"),
        )
    ).build(plan)
    review = ProcessActionSafetyValidator(
        registry,
        max_applications=1,
        max_processes=0,
    ).review(plan, blocked_preview)
    assert not review.approved
    assert any("blocked" in issue for issue in review.issues)
    assert any("batch" in issue for issue in review.issues)
