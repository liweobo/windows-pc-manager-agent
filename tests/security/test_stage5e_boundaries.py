from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.computer_tasks import ComputerTaskKind
from pc_manager_agent.domain.task_checkpoints import TaskCheckpoint
from pc_manager_agent.domain.task_workflows import DomainPreparationResult, DomainType
from pc_manager_agent.orchestration.domain_workflows import build_default_domain_workflow_registry
from pc_manager_agent.safety.final_orchestrator import SafeTaskSummaryPolicy


@pytest.mark.security
def test_registry_exposes_no_low_level_execution_or_confirmation_api() -> None:
    registry = build_default_domain_workflow_registry()
    forbidden = {"execute", "confirm", "run_command", "shell", "elevate", "undo_all"}
    for domain in DomainType:
        workflow = registry.require(domain)
        assert forbidden.isdisjoint(dir(workflow))


@pytest.mark.security
def test_checkpoint_cannot_restore_authority() -> None:
    with pytest.raises(ValidationError):
        TaskCheckpoint(
            task_id=uuid4(),
            graph_id=uuid4(),
            graph_version=1,
            graph_digest="a" * 64,
            policy_digest="b" * 64,
            nodes=(),
            checkpoint_reason="TEST_CHECKPOINT",
            authorization_restored=True,
        )


@pytest.mark.security
def test_sensitive_goal_summary_is_replaced_and_full_goal_not_persisted(
    runtime: ApplicationRuntime,
) -> None:
    goal = "api_key=" + "z" * 800
    assert SafeTaskSummaryPolicy().summarize(goal) == "受控电脑任务"
    task = runtime.tasks.orchestrator.create_task(
        goal,
        (DomainType.SYSTEM,),
        root_request_id=uuid4(),
        kind=ComputerTaskKind.ANALYSIS_ONLY,
        autonomy="PLAN_AND_ANALYZE",
    )
    assert task.safe_goal_summary == "受控电脑任务"
    connection = sqlite3.connect(runtime.settings.database_path)
    try:
        payloads = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT payload FROM computer_tasks UNION ALL "
                "SELECT payload FROM computer_task_graphs"
            )
            for value in row
        )
    finally:
        connection.close()
    assert goal not in payloads


@pytest.mark.security
def test_preparation_model_rejects_smuggled_execution_authority() -> None:
    with pytest.raises(ValidationError):
        DomainPreparationResult(
            task_id=uuid4(),
            node_id=uuid4(),
            graph_version=1,
            domain=DomainType.FILE,
            status="READY_FOR_REVIEW",
            requires_domain_confirmation=False,
            risk_level="R0",
            execution_authorized=True,
        )


@pytest.mark.security
def test_stage5e_source_has_no_global_executor_or_shell() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "pc_manager_agent"
    files = (
        root / "orchestration" / "final_orchestrator.py",
        root / "orchestration" / "domain_workflows.py",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "shell=True" not in text
    assert "subprocess" not in text
    assert "undo_all" not in text
