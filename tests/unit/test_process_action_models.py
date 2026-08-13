from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionType,
    ProcessTargetQuery,
    ProcessTargetQueryType,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.orchestration.process_action_planner import (
    is_process_action_request,
    preferred_process_action,
    process_target_query,
)
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from tests.stage4a_support import process_observation


def _plan(action: ProcessActionType = ProcessActionType.REQUEST_GRACEFUL_EXIT) -> ProcessActionPlan:
    observation = process_observation()
    target = ResolvedProcessTarget(
        display_name="demo",
        application_group_key="a" * 64,
        members=(observation,),
    )
    return ProcessActionPlan(
        user_goal="close demo",
        summary="controlled close",
        action=action,
        target_query=ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text="demo"),
        targets=(target,),
        risk_level=(
            RiskLevel.R2
            if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else RiskLevel.R2_HIGH_IMPACT
        ),
        estimated_processes_affected=1,
    )


def test_process_identity_digest_detects_pid_reuse_fields() -> None:
    first = process_observation()
    changed_time = process_observation(create_second=2)
    changed_path = process_observation(path=Path("C:/Users/test/App/other.exe"))
    assert first.identity.canonical_digest() != changed_time.identity.canonical_digest()
    assert first.identity.canonical_digest() != changed_path.identity.canonical_digest()


def test_action_plan_requires_exact_risk_confirmations_and_no_rollback() -> None:
    plan = _plan()
    with pytest.raises(ValidationError, match="risk"):
        ProcessActionPlan.model_validate(
            plan.model_copy(update={"risk_level": RiskLevel.R1}).model_dump(mode="python")
        )
    with pytest.raises(ValidationError, match="confirmation"):
        ProcessActionPlan.model_validate(
            plan.model_copy(update={"requires_runtime_confirmation": False}).model_dump(
                mode="python"
            )
        )


def test_preview_digest_cannot_be_tampered() -> None:
    plan = _plan()
    preview = ProcessPreviewEngine(
        ProcessSafetyPolicy(
            current_owner_sid="S-1-5-21-1000",
            current_session_id=1,
            windows_directory=Path("C:/Windows"),
        )
    ).build(plan)
    assert preview.executable
    with pytest.raises(ValidationError, match="digest"):
        ProcessActionPreview.model_validate(
            preview.model_copy(update={"target_set_digest": "f" * 64}).model_dump(mode="python")
        )


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("关闭 demo", ProcessActionType.REQUEST_GRACEFUL_EXIT),
        ("强制终止 demo", ProcessActionType.FORCE_TERMINATE),
        ("force kill demo", ProcessActionType.FORCE_TERMINATE),
    ],
)
def test_process_intent_is_deterministic(text: str, action: ProcessActionType) -> None:
    assert is_process_action_request(text)
    assert preferred_process_action(text) is action
    assert process_target_query(text).text == "demo"


def test_process_intent_rejects_vague_and_bulk_targets() -> None:
    with pytest.raises(ValueError, match="previous message"):
        process_target_query("关闭它")
    with pytest.raises(ValueError, match="bulk"):
        process_target_query("关闭所有进程")


def test_target_query_rejects_mixed_or_missing_selector() -> None:
    with pytest.raises(ValidationError):
        ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=42, text="wrong")
    with pytest.raises(ValidationError):
        ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, pid=42)
