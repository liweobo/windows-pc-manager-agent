from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pc_manager_agent.confirmation.process_actions import (
    ProcessActionConfirmationService,
    ProcessConfirmationError,
    ProcessConfirmationState,
)
from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionType,
    ProcessTargetQuery,
    ProcessTargetQueryType,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from tests.stage4a_support import process_observation


def _plan_preview() -> tuple[ProcessActionPlan, ProcessActionPreview]:
    observation = process_observation()
    plan = ProcessActionPlan(
        user_goal="close demo",
        summary="close",
        action=ProcessActionType.REQUEST_GRACEFUL_EXIT,
        target_query=ProcessTargetQuery(query_type=ProcessTargetQueryType.NAME, text="demo"),
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
    preview = ProcessPreviewEngine(
        ProcessSafetyPolicy(
            current_owner_sid="S-1-5-21-1000",
            current_session_id=1,
            windows_directory=Path("C:/Windows"),
        )
    ).build(plan)
    return plan, preview


def test_process_action_requires_two_same_action_confirmations_and_rejects_replay() -> None:
    service = ProcessActionConfirmationService()
    plan, preview = _plan_preview()
    first = service.request_plan(plan, preview)
    with pytest.raises(ProcessConfirmationError, match="approved same-action"):
        service.request_runtime(first.confirmation_id, plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    second = service.request_runtime(first.confirmation_id, plan, preview)
    service.resolve_runtime(second.confirmation_id, True, plan, preview)
    assert (
        service.consume_runtime(second.confirmation_id, plan, preview).state
        is ProcessConfirmationState.CONSUMED
    )
    with pytest.raises(ProcessConfirmationError, match="consumed"):
        service.consume_runtime(second.confirmation_id, plan, preview)


def test_expired_or_changed_runtime_confirmation_is_rejected() -> None:
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = ProcessActionConfirmationService(300, 10, now=lambda: clock[0])
    plan, preview = _plan_preview()
    first = service.request_plan(plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    second = service.request_runtime(first.confirmation_id, plan, preview)
    clock[0] += timedelta(seconds=10)
    with pytest.raises(ProcessConfirmationError, match="expired"):
        service.resolve_runtime(second.confirmation_id, True, plan, preview)

    service = ProcessActionConfirmationService()
    first = service.request_plan(plan, preview)
    with pytest.raises(ProcessConfirmationError, match="Preview"):
        service.resolve_plan(
            first.confirmation_id,
            True,
            plan,
            preview.model_copy(update={"preview_id": first.confirmation_id}),
        )


def test_confirmation_cannot_switch_graceful_plan_to_force_action() -> None:
    service = ProcessActionConfirmationService()
    plan, preview = _plan_preview()
    first = service.request_plan(plan, preview)
    service.resolve_plan(first.confirmation_id, True, plan, preview)
    force_plan = plan.model_copy(
        update={"action": ProcessActionType.FORCE_TERMINATE, "risk_level": RiskLevel.R2_HIGH_IMPACT}
    )
    with pytest.raises(ProcessConfirmationError):
        service.request_runtime(first.confirmation_id, force_plan, preview)
