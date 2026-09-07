from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.task_workflows import (
    DomainReconciliationRequest,
    DomainType,
    DomainWorkflowRequest,
)
from pc_manager_agent.orchestration.domain_workflows import (
    DomainWorkflowError,
    DomainWorkflowRegistry,
    UserInterfaceHandoffWorkflow,
    build_default_domain_workflow_registry,
)


def test_registry_is_complete_and_has_no_execute_method() -> None:
    registry = build_default_domain_workflow_registry()
    assert registry.domains() == tuple(DomainType)
    workflow = registry.require(DomainType.FILE)
    assert not hasattr(workflow, "execute")
    assert not hasattr(workflow, "confirm")


def test_registry_rejects_incomplete_domain_set() -> None:
    with pytest.raises(DomainWorkflowError):
        DomainWorkflowRegistry((UserInterfaceHandoffWorkflow(DomainType.FILE),))


def test_ui_handoff_and_reconciliation_never_replay() -> None:
    workflow = UserInterfaceHandoffWorkflow(DomainType.BROWSER)
    request = DomainWorkflowRequest(
        task_id=uuid4(),
        node_id=uuid4(),
        graph_version=1,
        domain=DomainType.BROWSER,
        goal_digest="a" * 64,
    )
    prepared = workflow.prepare(request)
    assert prepared.next_action_code == "OPEN_BROWSER_WORKFLOW"
    assert not prepared.execution_authorized
    reconciliation = workflow.reconcile(
        DomainReconciliationRequest(
            task_id=request.task_id,
            node_id=request.node_id,
            graph_version=1,
            domain=DomainType.BROWSER,
            domain_transaction_ref="browser-transaction",
        )
    )
    assert reconciliation.requires_user_decision
    assert not reconciliation.action_replayed
    recovery = workflow.recovery_summary("browser-transaction", RollbackLevel.MANUAL)
    assert recovery.rollback_level is RollbackLevel.MANUAL
    assert recovery.recovery_available
    assert recovery.recovery_action_code == "OPEN_BROWSER_RECOVERY"
    assert reconciliation.observed_at <= datetime.now(UTC)
