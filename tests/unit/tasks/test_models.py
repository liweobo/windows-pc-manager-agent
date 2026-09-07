from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.computer_tasks import (
    AutonomyLevel,
    ComputerTaskState,
    TaskBudget,
    TaskBudgetUsage,
    TaskPlanConfirmation,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_attention import (
    UserAttentionItem,
    UserAttentionKind,
)
from pc_manager_agent.domain.task_workflows import DomainType
from pc_manager_agent.orchestration.task_state_machine import (
    TaskStateMachine,
    TaskStateTransitionError,
)


def test_autonomy_excludes_full_unattended_and_budget_reports_exact_limits() -> None:
    assert "FULL_UNATTENDED" not in {item.value for item in AutonomyLevel}
    usage = TaskBudgetUsage(files_scanned=11, llm_calls=2)
    budget = TaskBudget(
        max_task_nodes=3,
        max_agent_calls=2,
        max_tool_preparations=2,
        max_browser_navigations=2,
        max_files_scanned=10,
        max_runtime_seconds=60,
        max_llm_calls=1,
        max_delegation_depth=1,
    )
    assert usage.exceeded(budget) == (
        "TASK_BUDGET_EXCEEDED_FILES_SCANNED",
        "TASK_BUDGET_EXCEEDED_LLM_CALLS",
    )


def test_task_plan_confirmation_cannot_authorize_domain_write() -> None:
    now = datetime.now(UTC)
    common = {
        "task_id": uuid4(),
        "graph_id": uuid4(),
        "graph_version": 1,
        "goal_digest": "a" * 64,
        "graph_digest": "b" * 64,
        "policy_digest": "c" * 64,
        "scope_digest": "d" * 64,
        "read_only_node_ids": (uuid4(),),
        "created_at": now,
        "expires_at": now + timedelta(minutes=1),
    }
    with pytest.raises(ValidationError):
        TaskPlanConfirmation(**common, domain_write_authorized=True)


def test_attention_notification_cannot_authorize() -> None:
    with pytest.raises(ValidationError):
        UserAttentionItem(
            task_id=uuid4(),
            graph_version=1,
            kind=UserAttentionKind.MANUAL_REVIEW,
            domain=DomainType.FILE,
            risk_level=RiskLevel.R0,
            title_code="MANUAL_REVIEW_REQUIRED",
            summary="Review",
            notification_can_authorize=True,
        )


def test_state_machine_rejects_terminal_and_skipped_transitions(
    task_services,
) -> None:
    template = task_services.templates.get("PC_HEALTH_CHECK")
    task = task_services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    machine = TaskStateMachine()
    with pytest.raises(TaskStateTransitionError):
        machine.transition(task, ComputerTaskState.COMPLETED)
